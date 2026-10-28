"""Explicit same-host restart of the constrained live OCI payment experiment.

Provider receipts and model decisions share the existing durable journal. An
unresolved provider reservation is not repeated and its usage remains unknown.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from benchlib.oci_model import OCIGenericModel, normalize, usage_counts, request_timeout, conversation
from services.payment_agent import AgentStopped, run_agent
from services.recoverable_agent import Journal, RecoverableTools, ReplayModel, atomic_json, digest, endpoint, owner
from services.worker import Client


def provider_usage(journal):
    records = journal.data.get('provider_requests', [])
    known = [usage_counts(r.get('metadata', {}).get('server_usage')) for r in records]
    unknown = sum(value is None for value in known)
    totals = {k: sum(value[k] for value in known if value is not None)
              for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return {'native_requests_reserved': len(records), 'unknown_usage_requests': unknown,
            'usage_complete': unknown == 0, 'known_server_usage_totals': totals,
            'server_usage_totals': totals if unknown == 0 else None}


class JournaledProvider:
    def __init__(self, journal, factory, emit=lambda event: None):
        self.journal, self.emit = journal, emit
        self.active = None
        settings = journal.data['context']['provider']
        self.budget = settings['request_budget']
        if type(self.budget) is not int or not 1 <= self.budget <= 6:
            raise ValueError('invalid_native_request_budget')
        records = journal.data.setdefault('provider_requests', [])
        if not isinstance(records, list) or len(records) > self.budget:
            raise ValueError('invalid_native_request_journal')
        for number, record in enumerate(records, 1):
            if record['number'] != number or type(record['logical_index']) is not int or not 0 <= record['logical_index'] < len(journal.data['model']):
                raise ValueError('invalid_provider_reservation')
            path = self.path(number)
            if path.exists():
                raw = path.read_bytes()
                if record.get('receipt_sha256') and record['receipt_sha256'] != hashlib.sha256(raw).hexdigest():
                    raise ValueError('provider_receipt_checksum_mismatch')
                self.accept(record, json.loads(raw), recovered=True)
            elif record.get('receipt_sha256'):
                raise ValueError('provider_receipt_missing')
        self.adapter = factory(self.prior, self.record)

    def path(self, number):
        return self.journal.path.parent / ('provider-' + str(number) + '.json')

    def prior(self):
        return [record['response'] for record in self.journal.data['model'] if 'response' in record]

    def accept(self, reservation, entry, recovered=False):
        settings = self.journal.data['context']['provider']
        index = reservation['logical_index']
        model_record = self.journal.data['model'][index]
        binding = {'number': reservation['number'], 'logical_index': index,
                   'body_sha256': model_record['body_sha256']}
        if (entry.get('journal_binding') != binding or reservation['body_sha256'] != binding['body_sha256'] or
                entry.get('requested_model_id') != settings['model_id'] or entry.get('region') != settings['region']):
            raise ValueError('provider_receipt_binding_mismatch')
        if any(entry.get(k) != settings[k] for k in ('read_timeout_s', 'untrusted_context_sha256', 'untrusted_context_bytes') if k in settings):
            raise ValueError('provider_receipt_settings_mismatch')
        reservation.update(status=entry['status'], metadata={k: v for k, v in entry.items() if k != 'response'},
                           receipt_sha256=hashlib.sha256(self.path(reservation['number']).read_bytes()).hexdigest())
        self.journal.save()
        if entry['status'] == 'received':
            response = entry['response']
            if (entry['returned_model_id'] != settings['model_id'] or
                    hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest() != entry['response_sha256'] or
                    normalize(response['chat_response']) != entry.get('normalized')):
                raise ValueError('provider_response_mismatch')
            if usage_counts(entry.get('server_usage')) is None:
                raise AgentStopped('provider_usage_missing')
            decision = {k: entry['normalized'][k] for k in ('final', 'tool_calls')}
            if 'response' in model_record and model_record['response'] != decision:
                raise ValueError('provider_decision_changed')
            model_record['response'] = decision
            self.journal.save()
        self.emit({'event': 'provider_receipt_recovered' if recovered else 'provider_receipt_recorded',
                   'number': reservation['number'], 'status': entry['status']})

    def record(self, value):
        reservation = self.active
        if reservation is None:
            raise ValueError('provider_receipt_without_reservation')
        entry = {**value, 'journal_binding': {k: reservation[k] for k in ('number', 'logical_index', 'body_sha256')}}
        path = self.path(reservation['number'])
        if path.exists():
            raise ValueError('provider_receipt_already_exists')
        # A crash between this file and the checkpoint is repaired on resume.
        atomic_json(path, entry)
        self.accept(reservation, entry)

    def __call__(self, body, deadline):
        records = self.journal.data['provider_requests']
        index = len(self.journal.data['model']) - 1
        fingerprint = digest(body)
        if index < 0 or self.journal.data['model'][index]['body_sha256'] != fingerprint:
            raise ValueError('provider_model_context_mismatch')
        if any(record['logical_index'] == index for record in records):
            raise AgentStopped('unresolved_provider_request')
        if len(records) >= self.budget:
            raise AgentStopped('durable_native_request_budget')
        self.journal.allow_action()
        reservation = {'number': len(records) + 1, 'logical_index': index, 'body_sha256': fingerprint, 'status': 'pending'}
        records.append(reservation);self.journal.save()
        self.active = reservation
        try:
            return self.adapter(body, deadline)
        finally:
            self.active = None


def identity(profile):
    import oci
    config = oci.config.from_file(profile_name=profile)
    token = Path(os.path.expanduser(config['security_token_file'])).read_text().strip()
    payload = token.split('.')[1]
    subject = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))['sub']
    signer = oci.auth.signers.SecurityTokenSigner(token, oci.signer.load_private_key_from_file(os.path.expanduser(config['key_file']), config.get('pass_phrase')))
    return config, signer, digest({'tenancy': config['tenancy'], 'subject': subject})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--model-region', required=True)
    parser.add_argument('--compartment-id', required=True)
    parser.add_argument('--profile', default='BENCHMARK')
    parser.add_argument('--engine-url', default=os.environ.get('BENCH_BASE_URL'))
    parser.add_argument('--support-url', default=os.environ.get('BENCH_SUPPORT_URL'))
    parser.add_argument('--case', choices=('authorized', 'unauthorized', 'over_limit', 'sanctions'), default='authorized')
    parser.add_argument('--wall-s', type=float, default=60)
    parser.add_argument('--read-timeout-s', type=float, default=20)
    parser.add_argument('--untrusted-context-file', type=Path)
    parser.add_argument('--bound-intent', type=Path, help='Operator-issued signed intent; opt-in restricted review routing')
    args = parser.parse_args()
    bound_intent = json.loads(args.bound_intent.read_text()) if args.bound_intent else None
    if bound_intent is not None and args.case != 'over_limit': parser.error('bound-intent currently requires case over_limit')
    if not 0 < args.wall_s <= 120:parser.error('wall-s must be positive and at most 120')
    try:request_timeout(1, args.read_timeout_s)
    except ValueError:parser.error('read-timeout-s must be positive, finite and at most 60')
    note = args.untrusted_context_file.read_text(encoding='utf-8') if args.untrusted_context_file else None
    conversation([], [], note)
    config, signer, principal = identity(args.profile)
    payment = {'authorized': args.case != 'unauthorized', 'approved': False, 'amount_minor': 2000 if args.case == 'over_limit' else 100,
               'limit_minor': 1000, 'sanctions_match': args.case == 'sanctions'}
    paths = [Path(__file__), Path(__file__).with_name('recoverable_agent.py'), Path(__file__).with_name('payment_agent.py'),
             Path(__file__).with_name('worker.py'), Path(__file__).resolve().parents[1] / 'benchlib/oci_model.py']
    context = {'engine_url': endpoint(args.engine_url), 'support_url': endpoint(args.support_url), 'payment': payment,
               'case': args.case, 'wall_s': args.wall_s, 'profile': args.profile, 'model_principal_sha256': principal,
               'workflow_principal_sha256': digest(os.environ['BENCH_USERNAME']),
               'provider': {'region': args.model_region, 'model_id': args.model_id, 'compartment_id': args.compartment_id,
                            'api_family': 'OCI Generic Chat', 'terminal_tool_policy': 'NONE', 'request_budget': 2,
                            'max_output_tokens': 256, 'stream': False, 'sdk_retries': False},
               'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    context['provider']['read_timeout_s'] = args.read_timeout_s
    if bound_intent is not None:
        from services.bound_payment import BoundAgentTools
        root = Path(__file__).resolve().parents[1]
        context['bound_intent_sha256'] = digest(bound_intent)
        context['sources'].update({p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in
                                  ('services/bound_payment.py','services/approval_agent.py','services/payment_approval.py',
                                   'workloads/bound-payment-approval.bpmn','workloads/agent-screen.bpmn','workloads/agent-direct.bpmn')})
    if note is not None:
        context['provider'].update(untrusted_context_sha256=hashlib.sha256(note.encode()).hexdigest(), untrusted_context_bytes=len(note.encode()))
    if not args.resume:args.output.mkdir(parents=True, exist_ok=False)
    elif not args.output.is_dir():parser.error('resume directory does not exist')
    with owner(args.output):
        journal = Journal(args.output, context, args.resume)
        journal.data['attempt'] += 1;journal.save();attempt = journal.data['attempt']
        with (args.output / ('events-attempt-' + str(attempt) + '.ndjson')).open('x', encoding='utf-8') as stream:
            def emit(event):
                stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **event}) + '\n');stream.flush();os.fsync(stream.fileno())
            auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ['BENCH_PASSWORD']).encode()).decode()
            engine = Client(context['engine_url'], auth, ca_file=os.environ.get('BENCH_ENGINE_CA_FILE'))
            support = Client(context['support_url'], 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN'], ca_file=os.environ.get('BENCH_SUPPORT_CA_FILE'))
            tools = (BoundAgentTools(engine, support, journal, emit, bound_intent) if bound_intent is not None
                     else RecoverableTools(engine, support, journal, emit))
            emit({'event': 'agent_resumed' if args.resume else 'agent_started', 'attempt': attempt, 'request_id': journal.data['request_id']})
            try:
                factory = lambda prior, record: OCIGenericModel(config, signer, args.compartment_id, args.model_id, args.model_region,
                    prior_responses=prior, record=record, max_requests=2, terminal_tool_policy='NONE', read_timeout_s=args.read_timeout_s, untrusted_context=note)
                provider = JournaledProvider(journal, factory, emit)
                result = run_agent(ReplayModel(journal, provider), tools, wall_s=args.wall_s, emit=emit)
            except AgentStopped as error:
                result = {'status': 'stopped', 'reason': str(error)}
            except Exception as error:
                result = {'status': 'unknown', 'error_type': type(error).__name__}
            journal.data['last_result'] = result;journal.save()
            emit({'event': 'agent_finished', **result})
        report = {'request_id': journal.data['request_id'], 'attempt': attempt, 'case': args.case, **result,
                  'model_request_reservations': journal.data['model_requests'], **provider_usage(journal),
                  'scope': 'Constrained live OCI model with explicit same-host journal recovery; operator security-token identity; no HA journal or infrastructure failover'}
        atomic_json(args.output / ('report-attempt-' + str(attempt) + '.json'), report)
        atomic_json(args.output / 'report.json', report)
        print(json.dumps(report))
        return 0 if result['status'] in ('settled', 'rejected', 'blocked_screening', 'pending_review', 'review_rejected', 'integrity_rejected', 'settlement_in_progress') else 1


if __name__ == '__main__':
    raise SystemExit(main())
