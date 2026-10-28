"""Opt-in recoverable agent routing with separate engine agent/reviewer accounts.

The model uses the existing bounded dispatcher. Trusted inputs and support token
remain in the adapter. Engine permissions restrict its separate REST identity.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import time

from services.payment_agent import AgentStopped, WorkflowTools, run_agent
from services.payment_approval import Approval
from services.recoverable_agent import Journal, RecoverableTools, ReplayModel, atomic_json, digest, endpoint, owner
from services.worker import Client


class DeadlineClient:
    def __init__(self, client, deadline):
        self.client, self.base, self.deadline = client, client.base, deadline

    def request(self, method, path, body=None):
        return WorkflowTools.request(self.client, method, path, body, self.deadline)


class ApprovalAgentTools(RecoverableTools):
    def __init__(self, engine, support, journal, emit):
        super().__init__(engine, support, journal, emit)
        p = self.payment
        if (set(p) != {'authorized', 'approved', 'amount_minor', 'limit_minor', 'sanctions_match'}
                or type(p['amount_minor']) is not int or p['amount_minor'] <= 0
                or type(p['limit_minor']) is not int or p['limit_minor'] != 1000
                or any(type(p[k]) is not bool for k in ('authorized', 'approved', 'sanctions_match'))
                or p['approved']):
            raise ValueError('invalid_trusted_agent_payment')

    def definition(self, scenario):
        return {'kyc': 'bench_agent_screen', 'settlement': 'bench_agent_direct'}[scenario]

    def __call__(self, scenario, deadline):
        if scenario != 'settlement' or self.payment['amount_minor'] <= 1000:
            return super().__call__(scenario, deadline)
        # The parent journal binds this directory and payment. The child journal
        # saves its random identity before any POST, including crash gaps.
        folder = self.journal.path.parent / 'review'
        folder.mkdir(exist_ok=True)
        with owner(folder):
            if not (folder / 'approval.json').exists():
                self.journal.allow_action()
            payment = {k: v for k, v in self.payment.items() if k != 'approved'}
            review = Approval(folder, DeadlineClient(self.engine, deadline), DeadlineClient(self.support, deadline), payment)
            if not review.data.get('start_intent'): self.journal.allow_action()
            result = review.start()
        self.emit({'event': 'payment_review_observed', **result})
        if result['state'] == 'completed': return result['outcome']
        return 'pending_review' if result['state'] == 'pending' else 'settlement_in_progress'


def run(directory, *, resume=False, case='over_limit', wall_s=30, bound_intent=None):
    if type(wall_s) not in (int, float) or not 0 < wall_s <= 120: raise ValueError('invalid_wall_s')
    engine_url, support_url = endpoint(os.environ['BENCH_BASE_URL']), endpoint(os.environ['BENCH_SUPPORT_URL'])
    username = os.environ['BENCH_USERNAME']
    payment = {'authorized': case not in ('unauthorized', 'over_limit_unauthorized'), 'approved': False,
               'amount_minor': 2000 if case.startswith('over_limit') else 100, 'limit_minor': 1000,
               'sanctions_match': case in ('sanctions', 'over_limit_sanctions')}
    root = Path(__file__).resolve().parents[1]
    paths = ['services/approval_agent.py', 'services/payment_approval.py', 'services/payment_agent.py',
             'services/recoverable_agent.py', 'services/worker.py', 'workloads/payment-approval.bpmn',
             'workloads/agent-screen.bpmn', 'workloads/agent-direct.bpmn']
    context = {'engine_url': engine_url, 'support_url': support_url, 'case': case, 'payment': payment,
               'wall_s': wall_s, 'workflow_principal_sha256': hashlib.sha256(username.encode()).hexdigest(),
               'sources': {p: hashlib.sha256((root / p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in paths}}
    if bound_intent is not None:
        from services.bound_payment import BoundAgentTools
        context['bound_intent_sha256'] = digest(bound_intent)
        context['sources'].update({p: hashlib.sha256((root/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
                                   for p in ('services/bound_payment.py','workloads/bound-payment-approval.bpmn')})
    directory = Path(directory)
    if not resume: directory.mkdir(parents=True, exist_ok=False)
    elif not (directory / 'checkpoint.json').is_file(): raise ValueError('resume_journal_missing')
    with owner(directory):
        journal = Journal(directory, context, resume)
        journal.data['attempt'] += 1; journal.save()
        engine = Client(engine_url, 'Basic ' + base64.b64encode((username + ':' + os.environ['BENCH_PASSWORD']).encode()).decode())
        support = Client(support_url, 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN'])
        events = []
        def emit(event):
            events.append(event)
            atomic_json(directory / ('events-attempt-' + str(journal.data['attempt']) + '.json'), events)
        tools = (BoundAgentTools(engine, support, journal, emit, bound_intent) if bound_intent is not None
                 else ApprovalAgentTools(engine, support, journal, emit))
        model = ReplayModel(journal, lambda body, deadline: tools.request(support, 'POST', '/model', body, deadline))
        result = run_agent(model, tools, wall_s=wall_s, emit=emit)
        journal.data['last_result'] = result; journal.save()
        report = {**result, 'request_id': journal.data['request_id'], 'attempt': journal.data['attempt'],
                  'model_fixture_requests_total': journal.data['model_requests'],
                  'review_directory': 'review' if (directory / 'review').exists() else None}
        atomic_json(directory / ('report-attempt-' + str(journal.data['attempt']) + '.json'), report)
        atomic_json(directory / 'report.json', report)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--case', choices=('authorized', 'unauthorized', 'over_limit', 'sanctions', 'over_limit_unauthorized', 'over_limit_sanctions'), default='over_limit')
    parser.add_argument('--wall-s', type=float, default=30)
    parser.add_argument('--bound-intent', type=Path)
    args = parser.parse_args()
    intent = json.loads(args.bound_intent.read_text()) if args.bound_intent else None
    print(json.dumps(run(args.output, resume=args.resume, case=args.case, wall_s=args.wall_s, bound_intent=intent)))
