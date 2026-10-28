"""Recover the deterministic payment-agent experiment from an atomic journal.

One OS-locked owner per output directory. Unknown engine starts are reconciled
by exact business key and definition; an absent/ambiguous match is never retried.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import uuid

from services.payment_agent import AgentStopped, WorkflowTools, run_agent
from services.worker import Client


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def owner(directory):
    """OS locks release even after SIGKILL; no expiring lease can overlap owners."""
    with (Path(directory) / 'owner.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Journal:
    def __init__(self, directory, context, resume=False):
        self.path = Path(directory) / 'checkpoint.json'
        if resume:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if self.data['version'] != 1 or self.data['context'] != context:
                raise ValueError('resume_context_mismatch')
        else:
            if self.path.exists():
                raise ValueError('journal_already_exists')
            self.data = {'version': 1, 'context': context, 'request_id': 'agent-' + uuid.uuid4().hex,
                         'action_deadline_utc_s': time.time() + context['wall_s'],
                         'model_requests': 0, 'model': [], 'workflows': {}, 'attempt': 0}
            self.save()

    def save(self):
        atomic_json(self.path, self.data)

    def allow_action(self):
        if time.time() >= self.data['action_deadline_utc_s']:
            raise AgentStopped('original_action_deadline')


class ReplayModel:
    def __init__(self, journal, call):
        self.journal, self.call, self.cursor = journal, call, 0

    def __call__(self, body, deadline):
        data = self.journal.data
        index = self.cursor;self.cursor += 1
        fingerprint = digest(body)
        if index < len(data['model']):
            record = data['model'][index]
            if record['body_sha256'] != fingerprint:
                raise ValueError('model_replay_context_mismatch')
        else:
            record = {'body_sha256': fingerprint}
            data['model'].append(record)
        if 'http_status' in record:
            raise urllib.error.HTTPError('fixture', record['http_status'], 'persisted', {}, None)
        if 'response' in record:
            return record['response']
        self.journal.allow_action()
        # Count before sending; a crash consumes the reservation, never resets it.
        if data['model_requests'] >= 6:
            raise AgentStopped('durable_model_request_budget')
        data['model_requests'] += 1;self.journal.save()
        try:
            action_deadline = time.monotonic() + max(0, data['action_deadline_utc_s'] - time.time())
            response = self.call(body, min(deadline, action_deadline))
        except urllib.error.HTTPError as error:
            if error.code == 429:
                record['http_status'] = 429;self.journal.save()
            raise
        if not isinstance(response, dict) or len(json.dumps(response).encode()) > 16384:
            raise AgentStopped('invalid_model_response')
        # Persist only fixture control fields; model prose is not needed to resume.
        record['response'] = {k: response[k] for k in ('final', 'tool_calls') if k in response}
        self.journal.save()
        return record['response']


class RecoverableTools(WorkflowTools):
    def __init__(self, engine, support, journal, emit):
        self.journal = journal
        super().__init__(engine, support, journal.data['request_id'], journal.data['context']['payment'], emit)

    def request(self, client, method, path, body, deadline):
        if client is not self.engine or method != 'POST' or not path.endswith('/start'):
            return super().request(client, method, path, body, deadline)
        definition = path.rsplit('/', 2)[1]
        scenario = next((s for s in ('kyc', 'settlement') if self.definition(s) == definition), None)
        if scenario is None or body['businessKey'] != self.request_id + ':' + scenario:
            raise ValueError('unexpected_start_identity')
        workflows = self.journal.data['workflows']
        record = workflows.get(scenario)
        fingerprint = digest(body)
        if record is None:
            self.journal.allow_action()
            record = {'body_sha256': fingerprint, 'intent': True}
            workflows[scenario] = record;self.journal.save()
            action_deadline = time.monotonic() + max(0, self.journal.data['action_deadline_utc_s'] - time.time())
            instance = super().request(client, method, path, body, min(deadline, action_deadline))
            record['process_instance_id'] = instance['id'];self.journal.save()
            return instance
        if record['body_sha256'] != fingerprint:
            raise ValueError('workflow_replay_context_mismatch')
        if not record.get('process_instance_id'):
            query = urllib.parse.urlencode({'processInstanceBusinessKey': body['businessKey'], 'processDefinitionKey': definition})
            matches = super().request(client, 'GET', '/history/process-instance?' + query, None, deadline)
            if len(matches) != 1 or matches[0].get('businessKey') != body['businessKey'] or matches[0].get('processDefinitionKey') != definition:
                raise AgentStopped('unresolved_workflow_start')
            record['process_instance_id'] = matches[0]['id'];self.journal.save()
        self.emit({'event': 'workflow_reconciled', 'scenario': scenario, 'process_instance_id': record['process_instance_id']})
        return {'id': record['process_instance_id']}

    def __call__(self, scenario, deadline):
        result = super().__call__(scenario, deadline)
        record = self.journal.data['workflows'][scenario]
        if 'verified_result' in record and record['verified_result'] != result:
            raise ValueError('verified_result_changed')
        record['verified_result'] = result;self.journal.save()
        return result


def endpoint(value):
    parsed = urllib.parse.urlsplit(value or '')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid_endpoint')
    return value.rstrip('/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--engine-url', default=os.environ.get('BENCH_BASE_URL'))
    parser.add_argument('--support-url', default=os.environ.get('BENCH_SUPPORT_URL'))
    parser.add_argument('--case', choices=('authorized', 'unauthorized', 'over_limit', 'sanctions'), default='authorized')
    parser.add_argument('--mode', choices=('normal', 'throttle', 'invalid_tool', 'loop'), default='normal')
    parser.add_argument('--wall-s', type=float, default=30)
    args = parser.parse_args()
    if not 0 < args.wall_s <= 120:
        parser.error('wall-s must be positive and at most 120')
    engine_url, support_url = endpoint(args.engine_url), endpoint(args.support_url)
    payment = {'authorized': args.case != 'unauthorized', 'approved': False,
               'amount_minor': 2000 if args.case == 'over_limit' else 100,
               'limit_minor': 1000, 'sanctions_match': args.case == 'sanctions'}
    context = {'engine_url': engine_url, 'support_url': support_url, 'case': args.case, 'mode': args.mode,
               'payment': payment, 'wall_s': args.wall_s,
               'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__), Path(__file__).with_name('payment_agent.py'), Path(__file__).with_name('worker.py'))}}
    if args.resume:
        if not args.output.is_dir():
            parser.error('resume output directory does not exist')
    else:
        args.output.mkdir(parents=True, exist_ok=False)
    with owner(args.output):
        journal = Journal(args.output, context, args.resume)
        journal.data['attempt'] += 1;journal.save()
        attempt = journal.data['attempt']
        with (args.output / ('events-attempt-' + str(attempt) + '.ndjson')).open('x', encoding='utf-8') as stream:
            def emit(event):
                stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **event}) + '\n');stream.flush();os.fsync(stream.fileno())
            auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ['BENCH_PASSWORD']).encode()).decode()
            engine = Client(engine_url, auth, ca_file=os.environ.get('BENCH_ENGINE_CA_FILE'))
            support = Client(support_url, 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN'], ca_file=os.environ.get('BENCH_SUPPORT_CA_FILE'))
            tools = RecoverableTools(engine, support, journal, emit)
            model = ReplayModel(journal, lambda body, deadline: tools.request(support, 'POST', '/model', body, deadline))
            emit({'event': 'agent_resumed' if args.resume else 'agent_started', 'request_id': journal.data['request_id'], 'attempt': attempt})
            try:
                # Existing effects may be reconciled after the action deadline.
                # Replay never grants a new action budget or extra model requests.
                result = run_agent(model, tools, mode=args.mode, wall_s=args.wall_s, emit=emit)
            except AgentStopped as error:
                result = {'status': 'stopped', 'reason': str(error)}
            except Exception as error:
                result = {'status': 'unknown', 'error_type': type(error).__name__}
            journal.data['last_result'] = result;journal.save()
            emit({'event': 'agent_finished', **result})
        report = {'request_id': journal.data['request_id'], 'attempt': attempt, 'case': args.case, 'mode': args.mode,
                  'model_requests_total': journal.data['model_requests'], **result,
                  'scope': 'Single-owner local journal recovery, deterministic model fixture, real Fluxnova workflows; no live model, HA journal or human approval lifecycle'}
        atomic_json(args.output / ('report-attempt-' + str(attempt) + '.json'), report)
        atomic_json(args.output / 'report.json', report)
        print(json.dumps(report))
        return 0 if result['status'] in ('settled', 'rejected', 'blocked_screening') else 1


if __name__ == '__main__':
    raise SystemExit(main())
