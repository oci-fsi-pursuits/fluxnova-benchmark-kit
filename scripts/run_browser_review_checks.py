#!/usr/bin/env python3
"""Two synthetic Tasklist decisions with temporary scoped identities.

Requires Playwright, installed Chrome, the private web/support/verifier forwards,
the administrator BENCH_* environment and a valid OCI session for Vault access.
Only setup/assignment and independent validation use REST directly. Decisions
use visible Tasklist controls, without injected JavaScript or API completion.
No native model requests, saved browser cookies, HAR files or password files.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlencode, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.bound_payment import BENEFICIARY, BoundReview
from services.live_recoverable_agent import identity
from services.payment_approval import clients, snapshot, typed
from services.recoverable_agent import atomic_json, digest
from services.worker import Client
from scripts.run_persistent_verifier_checks import READS


def run(args):
    if not 0 <= args.slow_mo_ms <= 1000 or not 0 <= args.demo_hold_s <= 10:
        raise ValueError('presentation_pacing_out_of_range')
    decisions = ('approve', 'reject') if args.cases == 'both' else (args.cases,)
    if args.playwright_path:
        sys.path.insert(0, str(args.playwright_path.resolve()))
    from playwright.sync_api import sync_playwright, expect
    import importlib.metadata
    import oci

    engine, support = clients()
    for url in (args.web_url, args.authority_url, engine.base, support.base):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username or parsed.query or parsed.fragment):
            raise ValueError('browser_checks_require_private_local_forwards')
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.verifier_config.read_text())
    cfg, signer, _ = identity(args.profile)
    vault = oci.secrets.SecretsClient(cfg, signer=signer, timeout=(10, 30), retry_strategy=oci.retry.NoneRetryStrategy())
    response = vault.get_secret_bundle(config['secret_id'], version_number=config['secret_version'],
                                      retry_strategy=oci.retry.NoneRetryStrategy())
    values = json.loads(base64.b64decode(response.data.secret_bundle_content.content))
    authority = Client(args.authority_url, 'Bearer ' + values['management_token'])
    health = Client(args.authority_url).request('GET', '/health')
    if not health['ready'] or health['vault_version'] != config['secret_version']:
        raise ValueError('persistent_verifier_not_ready')
    names = {role: 'browser' + role + uuid.uuid4().hex[:8] for role in ('agent', 'reviewer')}
    passwords = {role: secrets.token_urlsafe(32) for role in names}
    users, grants, filters, started, results = [], [], [], [], []
    automatic_filter_grants = []
    plan = {'cases': list(decisions), 'users': names, 'native_requests_max': 0,
            'review_assignment': 'administrator assigns to the registered reviewer before UI entry',
            'browser': 'installed Chrome in an isolated ' + ('visible' if args.headed else 'headless') + ' context',
            'presentation': {'slow_mo_ms': args.slow_mo_ms, 'hold_s': args.demo_hold_s,
                             'video_requested': args.record_video},
            'playwright_version': importlib.metadata.version('playwright'),
            'form_mode': args.form_mode,
            'decisions': 'Tasklist form, one Complete click per case; no retry',
            'vault_version': config['secret_version'], 'health_before': health}
    atomic_json(output / 'plan.json', plan)

    def save():
        atomic_json(output / 'progress.json', {'users': users, 'grants': grants, 'filters': filters,
                    'automatic_filter_grants': automatic_filter_grants, 'started': started, 'results': results})

    def grant(role, resource, resource_id, permissions):
        grants.append(engine.request('POST', '/authorization/create', {
            'type': 1, 'userId': names[role], 'resourceType': resource,
            'resourceId': resource_id, 'permissions': permissions}))
        save()

    def invoke(folder, intent_file, resume=False):
        env = {**os.environ, 'BENCH_USERNAME': names['agent'], 'BENCH_PASSWORD': passwords['agent'],
               'PYTHONPATH': str(ROOT)}
        command = [sys.executable, '-m', 'services.approval_agent', '--output', str(folder),
                   '--case', 'over_limit', '--bound-intent', str(intent_file), '--wall-s', '90']
        if resume:
            command.append('--resume')
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=120)
        folder.mkdir(parents=True, exist_ok=True)
        number = len(list(folder.glob('browser-driver-*.log'))) + 1
        (folder / ('browser-driver-' + str(number) + '.log')).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('browser_agent_driver_failed')
        return json.loads((folder / 'report.json').read_text())

    try:
        for role, user in names.items():
            engine.request('POST', '/user/create', {'profile': {'id': user, 'firstName': 'Browser', 'lastName': role},
                                                   'credentials': {'password': passwords[role]}})
            users.append(user)
            save()
            definitions = ('bench_agent_screen', 'bench_agent_direct', 'bench_bound_payment_approval') if role == 'agent' else ('bench_bound_payment_approval',)
            for definition in definitions:
                grant(role, 6, definition, READS + (['CREATE_INSTANCE'] if role == 'agent' else ['TASK_WORK']))
            if role == 'agent':
                grant(role, 8, '*', ['CREATE'])
        grant('reviewer', 0, 'tasklist', ['ACCESS'])
        filter_row = engine.request('POST', '/filter/create', {
            'resourceType': 'Task', 'name': 'Browser synthetic payment review', 'owner': names['reviewer'],
            'query': {'processDefinitionKey': 'bench_bound_payment_approval', 'assignee': names['reviewer']},
            'properties': {'description': 'Temporary isolated browser review validation', 'priority': 1}})
        filters.append(filter_row['id'])
        save()
        # The engine automatically grants the filter owner access; do not create
        # a duplicate authorization for this same user/resource pair.
        automatic_filter_grants.extend(engine.request('GET', '/authorization?' + urlencode({
            'userIdIn': names['reviewer'], 'resourceType': 5, 'resourceId': filter_row['id']})))
        save()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=not args.headed,
                                                 slow_mo=args.slow_mo_ms)
            context = None
            try:
                context_options = {'viewport': {'width': 1440, 'height': 900 if args.record_video else 1100}}
                if args.record_video:
                    context_options.update(record_video_dir=str(output / 'video'),
                                           record_video_size={'width': 1440, 'height': 900})
                context = browser.new_context(**context_options)
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto(args.web_url, wait_until='networkidle')
                page.get_by_placeholder('Username').fill(names['reviewer'])
                page.get_by_placeholder('Password').fill(passwords['reviewer'])
                page.get_by_role('button', name='Log in', exact=True).click()
                expect(page.get_by_placeholder('Username')).to_have_count(0)
                for decision in decisions:
                    folder = output / decision
                    folder.mkdir()
                    payment = {'amount_minor': 2000, 'limit_minor': 1000, 'authorized': True, 'sanctions_match': False}
                    issue = {'request_id': 'browser-' + uuid.uuid4().hex, 'payment': payment,
                             'reviewer': names['reviewer'], 'ttl_s': 3600}
                    intent = authority.request('POST', '/intents', issue)
                    intent_file = folder / 'intent.local.json'
                    atomic_json(folder / 'issuance-request.json', issue)
                    atomic_json(intent_file, intent)
                    row = {'decision': decision, 'key': intent['key']}
                    started.append(row)
                    save()
                    initial = invoke(folder / 'agent', intent_file)
                    if initial['status'] != 'pending_review':
                        raise ValueError('browser_review_not_pending')
                    checkpoint = json.loads((folder / 'agent/checkpoint.json').read_text())
                    pending = BoundReview(folder / 'agent/review', engine, support, intent).status()
                    row.update(process_id=pending['process_id'], task_id=pending['task_id'],
                               screening_id=checkpoint['workflows']['kyc']['process_instance_id'],
                               screening_key=checkpoint['request_id'] + ':kyc')
                    save()
                    authority.request('POST', '/intents/' + intent['key'] + '/bind', {
                        'process_id': row['process_id'], 'screening_id': row['screening_id'], 'screening_key': row['screening_key']})
                    engine.request('POST', '/task/' + row['task_id'] + '/assignee', {'userId': names['reviewer']})
                    before = support.request('GET', '/state/' + intent['key'])
                    if before['effect_count'] != 0:
                        raise ValueError('payment_effect_before_review')
                    page.reload(wait_until='networkidle')
                    page.locator('a[href="#/?task=' + row['task_id'] + '"]').click()
                    validation = {}
                    if args.form_mode == 'payment':
                        expect(page.locator('#payment-review-title')).to_have_text('Review synthetic payment')
                        for selector, text in {
                            '#review-payment-amount': str(payment['amount_minor']),
                            '#review-payment-limit': str(payment['limit_minor']),
                            '#review-payment-beneficiary': BENEFICIARY,
                            '#review-payment-reference': intent['key'],
                            '#review-payment-authorization': 'Authorized',
                            '#review-payment-sanctions': 'No match flagged',
                        }.items():
                            expect(page.locator(selector)).to_have_text(text)
                        expect(page.locator('#bound-payment-review input:not([type="checkbox"])')).to_have_count(0)
                        expect(page.locator('input[fxn-variable-name="boundIntent"]')).to_have_count(0)
                        complete = page.get_by_role('button', name='Complete', exact=True)
                        choice = page.get_by_label('Your decision', exact=True)
                        acknowledgment = page.get_by_label('I have reviewed the payment details above and confirm my decision.', exact=True)
                        expect(complete).to_be_disabled()
                        validation['empty_decision_and_acknowledgment_blocked'] = True
                        if args.demo_hold_s:
                            print('Demo: payment is waiting for a separate reviewer; no effect yet.', flush=True)
                            page.wait_for_timeout(args.demo_hold_s * 1000)
                        choice.select_option(decision)
                        expect(complete).to_be_disabled()
                        validation['decision_without_acknowledgment_blocked'] = True
                        acknowledgment.check()
                        choice.select_option('')
                        expect(complete).to_be_disabled()
                        validation['acknowledgment_without_decision_blocked'] = True
                        choice.select_option(decision)
                        expect(complete).to_be_enabled()
                        validation['explicit_decision_and_acknowledgment_enabled'] = True
                        validation['payment_details_have_no_editable_inputs'] = True
                        validation['bound_intent_not_rendered'] = True
                    else:
                        page.locator('a:has-text("Load Variables"):visible').click()
                        for key, value in {**snapshot(payment, intent['key']), 'paymentBeneficiary': BENEFICIARY,
                                           'boundIntent': intent['token'], 'reviewerDecision': 'pending'}.items():
                            field = page.locator('input[fxn-variable-name="' + key + '"]')
                            if type(value) is bool:
                                expect(field).to_be_checked(checked=value)
                            else:
                                expect(field).to_have_value(str(value))
                        expect(page.locator('#business-key-field')).to_have_value(intent['key'])
                        page.locator('input[fxn-variable-name="reviewerDecision"]').fill(decision)
                        for name, value in {'reviewedPaymentSha256': digest(payment), 'reviewerIdentity': names['reviewer']}.items():
                            page.locator('a:has-text("Add a variable"):visible').click()
                            page.locator('input[name="name"]:visible').last.fill(name)
                            page.locator('select:visible').last.select_option('String')
                            page.locator('input[fxn-variable-name="' + name + '"]').fill(value)
                    page.screenshot(path=str(folder / 'before-submit.png'), full_page=True,
                                    mask=[page.locator('input[fxn-variable-name="boundIntent"]')])
                    submits = []

                    def submission(response):
                        request = response.request
                        path = urlsplit(response.url).path
                        if request.method == 'POST' and '/task/' + row['task_id'] + '/' in path and path.rsplit('/', 1)[-1] in ('submit-form', 'complete'):
                            body = request.post_data_json
                            submits.append({'path': path, 'method': request.method, 'status': response.status,
                                            'variable_names': sorted(body.get('variables', {})),
                                            'payment_ack_sha256': body.get('variables', {}).get('reviewedPaymentSha256', {}).get('value'),
                                            'decision': body.get('variables', {}).get('reviewerDecision', {}).get('value')})

                    page.on('response', submission)
                    if args.demo_hold_s:
                        page.wait_for_timeout(args.demo_hold_s * 1000)
                    # No retry of a possibly committed completion, even if the
                    # browser navigation or subsequent independent check fails.
                    page.get_by_role('button', name='Complete', exact=True).click()
                    page.wait_for_load_state('networkidle')
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        history = engine.request('GET', '/history/process-instance/' + row['process_id'])
                        if history.get('endTime'):
                            break
                        page.wait_for_timeout(250)
                    if not history.get('endTime') or history['state'] != 'COMPLETED':
                        raise ValueError('browser_payment_not_completed')
                    verified = BoundReview(folder / 'agent/review', engine, support, intent).status()
                    expected = 'settled' if decision == 'approve' else 'review_rejected'
                    if verified['outcome'] != expected or verified['effect_count'] != int(decision == 'approve'):
                        raise ValueError('browser_payment_outcome_mismatch')
                    ledger = support.request('GET', '/state/' + intent['key'])
                    operations = engine.request('GET', '/history/user-operation?' + urlencode({'processInstanceId': row['process_id']}))
                    completed = [op for op in operations if op.get('taskId') == row['task_id'] and op.get('operationType') == 'Complete']
                    if not completed or any(op.get('userId') != names['reviewer'] for op in completed):
                        raise ValueError('browser_review_actor_mismatch')
                    page.reload(wait_until='networkidle')
                    expect(page.get_by_role('button', name='Complete', exact=True)).to_have_count(0)
                    page.screenshot(path=str(folder / 'after-reload.png'), full_page=True)
                    page.remove_listener('response', submission)
                    if (len(submits) != 1 or submits[0]['status'] not in (200, 204)
                            or submits[0]['decision'] != decision or submits[0]['payment_ack_sha256'] != digest(payment)):
                        raise ValueError('browser_submission_count_or_response_mismatch')
                    if args.form_mode == 'payment' and submits[0]['variable_names'] != ['reviewedPaymentSha256', 'reviewerDecision']:
                        raise ValueError('payment_form_submitted_unexpected_fields')
                    final = invoke(folder / 'agent', intent_file, resume=True)
                    replay = invoke(folder / 'agent', intent_file, resume=True)
                    if any(report['status'] != expected or report['model_fixture_requests_total'] != 2 for report in (final, replay)):
                        raise ValueError('browser_agent_replay_mismatch')
                    if support.request('GET', '/state/' + intent['key']) != ledger:
                        raise ValueError('browser_replay_changed_ledger')
                    result = {**row, 'status': 'pass', 'verified': verified, 'history': history, 'operations': operations,
                              'ledger': ledger, 'displayed_payment_matches': True, 'browser_submissions': submits,
                              'ledger_unchanged_after_browser_reload_and_agent_replay': True,
                              'final': final, 'replay': replay, 'browser_version': browser.version,
                              'form_mode': args.form_mode, 'form_validation': validation}
                    atomic_json(folder / 'result.json', result)
                    results.append(result)
                    save()
                    print(json.dumps({'case': decision, 'status': 'pass', 'effects': verified['effect_count']}), flush=True)
                    if args.demo_hold_s:
                        page.wait_for_timeout(args.demo_hold_s * 1000)
            finally:
                # Closing the context flushes Playwright's video before browser shutdown.
                if context is not None:
                    context.close()
                browser.close()
        queues = {name: engine.request('GET', path)['count'] for name, path in (
            ('active_instances', '/process-instance/count'), ('external_tasks', '/external-task/count'), ('incidents', '/incident/count'))}
        if any(queues.values()):
            raise ValueError('browser_check_queues_not_empty')
        atomic_json(output / 'summary.json', {'status': 'pass', 'results': results, 'queues': queues,
                    'native_requests': 0, 'health_after': Client(args.authority_url).request('GET', '/health')})
    except Exception as error:
        atomic_json(output / 'failure.json', {'error_type': type(error).__name__,
                    'message': str(error) if isinstance(error, ValueError) else None})
        raise
    finally:
        cleanup = {'closed_pending': [], 'filters_deleted': [], 'grants_deleted': [], 'users_deleted': [], 'failures': []}
        for row in started:
            if 'process_id' not in row:
                continue
            try:
                for task in engine.request('GET', '/task?' + urlencode({'processInstanceId': row['process_id']})):
                    engine.request('POST', '/task/' + task['id'] + '/complete', {'variables': typed({'reviewerDecision': 'reject'})})
                    cleanup['closed_pending'].append(row['process_id'])
            except Exception as error:
                cleanup['failures'].append({'action': 'close_pending', 'error': type(error).__name__})
        for grant_row in reversed(grants):
            try:
                engine.request('DELETE', '/authorization/' + grant_row['id'])
                cleanup['grants_deleted'].append(grant_row['id'])
            except Exception as error:
                cleanup['failures'].append({'action': 'delete_grant', 'error': type(error).__name__})
        for filter_id in filters:
            try:
                engine.request('DELETE', '/filter/' + filter_id)
                cleanup['filters_deleted'].append(filter_id)
            except Exception as error:
                cleanup['failures'].append({'action': 'delete_filter', 'error': type(error).__name__})
        for user in reversed(users):
            try:
                engine.request('DELETE', '/user/' + user)
                cleanup['users_deleted'].append(user)
            except Exception as error:
                cleanup['failures'].append({'action': 'delete_user', 'error': type(error).__name__})
        atomic_json(output / 'cleanup.json', cleanup)
        if cleanup['failures']:
            raise RuntimeError('browser_check_cleanup_failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verifier-config', type=Path, required=True)
    parser.add_argument('--profile', default='BENCHMARK')
    parser.add_argument('--authority-url', default='http://127.0.0.1:19082')
    parser.add_argument('--web-url', default='http://127.0.0.1:19080/fluxnova/app/tasklist/default/')
    parser.add_argument('--playwright-path', type=Path, help='Optional workspace-local Playwright package directory')
    parser.add_argument('--form-mode', choices=('payment', 'generic'), default='payment')
    parser.add_argument('--cases', choices=('approve', 'reject', 'both'), default='both')
    parser.add_argument('--headed', action='store_true', help='Show the automated Tasklist walkthrough')
    parser.add_argument('--record-video', action='store_true', help='Save browser video under OUTPUT/video')
    parser.add_argument('--slow-mo-ms', type=int, default=0, help='Delay browser actions, 0 to 1000 ms')
    parser.add_argument('--demo-hold-s', type=float, default=0, help='Pause at review and result, 0 to 10 seconds')
    run(parser.parse_args())
