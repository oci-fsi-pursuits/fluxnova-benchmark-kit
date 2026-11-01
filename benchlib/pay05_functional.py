"""Run the fixed PAY-05 functional corpus against an already owned engine."""
import concurrent.futures
import json
import queue
import secrets
import threading
import time
import urllib.error
import urllib.parse

from scripts.audit_pay05_functional import check_record, resolved_case
from scripts.run_w02_functional import require, save
from services.fx_quotes import ACTIVITIES, OPERATIONS, TOPIC, QuoteStore
from services.worker import Client
from services.workflow_checks import make_server
from services.workflow_checks_worker import WorkerPool


def exercise(engine, output, corpus, run_id, order, summary, url, authorization, *, bound=lambda: None):
    """Collect original records and stop only this collector's backend/workers."""
    server = pool = None
    try:
        store = QuoteStore(output / 'study.sqlite')
        worker_token, registrar_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        server = make_server('127.0.0.1', 0, store, worker_token, registrar_token, service_name='pay05-synthetic-quotes')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        support_url = 'http://127.0.0.1:' + str(server.server_port)
        support = Client(support_url, 'Bearer ' + worker_token)
        registrar = Client(support_url, 'Bearer ' + registrar_token)
        def workers():
            # Build TLS/client contexts before any worker can fetch. Otherwise a
            # short functional overlap probe also includes unequal cold startup.
            engines, dependencies = queue.SimpleQueue(), queue.SimpleQueue()
            for _ in range(3):
                engines.put(Client(url, authorization))
                dependencies.put(Client(support_url, 'Bearer ' + worker_token))
            return WorkerPool(engines.get_nowait, dependencies.get_nowait,
                              slots=3, idle_s=.1, topic=TOPIC, activities=ACTIVITIES, error_prefix='pay05').start()
        approved = {}
        for source in corpus['cases']:
            bound()
            for arm in order[source['case_id']]:
                key = 'pay05-' + run_id[:12] + '-' + arm + '-' + source['case_id']
                case = resolved_case(source, key)
                started = engine.start('bench_pay05_' + arm, key, {'studyCaseKey': {'value': key, 'type': 'String'}})
                pid = started['id']
                registrar.request('POST', '/cases/register', {'case_key': key, 'process_id': pid, 'case': case})
                try:
                    support.request('POST', '/tools/execute', {'case_key': key, 'process_id': pid, 'operation': 'select'})
                    raise AssertionError('premature_effect_accepted')
                except urllib.error.HTTPError as error: require(error.code == 409, 'unexpected_premature_response')
                pool = workers(); observed = engine.observe(pid, timeout_s=20, poll_s=.1)
                pool.stop(); records = pool.records; pool = None
                before = registrar.request('GET', '/state/' + urllib.parse.quote(key, safe=''))
                activities = engine.request('GET', '/history/activity-instance?processInstanceId=' + pid)
                definition = engine.request('GET', '/process-definition/' + started['definitionId'])
                record = {'case': case, 'arm': arm, 'case_key': key, 'process_id': pid, 'history': observed,
                          'definition': definition, 'activities': activities, 'backend_before_replay': before, 'worker_records': records,
                          'premature_effect_refused': True}
                save(output / (arm + '-' + source['case_id'] + '.raw.json'), record)
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    repeats = list(executor.map(lambda op: support.request('POST', '/tools/execute',
                        {'case_key': key, 'process_id': pid, 'operation': op}), list(OPERATIONS) * 2))
                require(all(r['replayed'] for r in repeats), 'replay_reexecuted_operation')
                record.update(backend_after_replay=registrar.request('GET', '/state/' + urllib.parse.quote(key, safe='')), replay_calls=len(repeats))
                save(output / (arm + '-' + source['case_id'] + '.json'), record)
                row = check_record(case, record); summary['cases'].append(row); save(output / 'progress.json', summary)
                print(json.dumps({'case': source['case_id'], 'arm': arm, 'status': 'passed', 'selected_route': row['selected_route']}), flush=True)
                if row['outcome'] == 'settled' and arm not in approved: approved[arm] = (key, before)
        for arm, (key, original) in approved.items():
            bound()
            replay = engine.start('bench_pay05_' + arm, key, {'studyCaseKey': {'value': key, 'type': 'String'}})
            pid = replay['id']; pool = workers(); deadline = time.monotonic() + 10
            while True:
                tasks = engine.request('GET', '/external-task?processInstanceId=' + pid)
                if tasks and all(t.get('retries') == 0 for t in tasks): break
                require(time.monotonic() < deadline, 'cross_process_replay_not_blocked'); time.sleep(.1)
            pool.stop(); pool = None
            after = registrar.request('GET', '/state/' + urllib.parse.quote(key, safe=''))
            require(after['effects'] == original['effects'] and after['receipts'] == original['receipts'], 'cross_process_replay_changed_result')
            incidents = engine.request('GET', '/incident?processInstanceId=' + pid)
            require(len(incidents) == len(tasks), 'replay_incident_missing')
            engine.request('DELETE', '/process-instance/' + pid + '?skipCustomListeners=true&skipIoMappings=true')
            probe = {'arm': arm, 'process_id': pid, 'blocked': True, 'incidents': incidents,
                     'original_effects_unchanged': True, 'cleanup': 'deleted_only_owned_replay_instance'}
            save(output / ('replay-' + arm + '.json'), {'probe': probe, 'tasks': tasks, 'backend_after': after})
            summary['replay_probes'].append(probe)
        counts = {k: engine.request('GET', p)['count'] for k, p in [('active_instances', '/process-instance/count'),
                  ('external_tasks', '/external-task/count'), ('incidents', '/incident/count')]}
        save(output / 'final-engine-counts.json', counts); require(all(v == 0 for v in counts.values()), 'engine_not_drained')
        summary.update(status='passed', functional_executions=len(summary['cases']), synthetic_effects=sum(r['effects'] for r in summary['cases']))
    finally:
        errors = []
        for name, action in ([('workers', pool.stop)] if pool else []) + ([('backend_shutdown', server.shutdown), ('backend_close', server.server_close)] if server else []):
            try: action()
            except Exception as error: errors.append({'component': name, 'error_type': type(error).__name__})
        summary['collector_cleanup_errors'] = errors
        summary['collector_stopped'] = not errors
        if errors:
            summary['status'] = 'failed'
            raise RuntimeError('collector_cleanup_incomplete')
