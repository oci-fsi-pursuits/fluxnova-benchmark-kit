"""Engine-durable synthetic payment review; simulated reviewer, not production RBAC.

The journal binds the review to trusted payment inputs. One owner, no blind retry
of ambiguous starts or task completions. Administrator access can bypass this
driver; candidateGroups alone does not establish authorization separation.
"""
import argparse
import base64
import json
import hashlib
import os
from pathlib import Path
import time
import urllib.parse
import uuid

from services.recoverable_agent import atomic_json, digest, owner
from services.worker import Client, task_request

DEFINITION = 'bench_payment_approval'


def snapshot(payment, key):
    if set(payment) != {'amount_minor', 'limit_minor', 'authorized', 'sanctions_match'}:
        raise ValueError('invalid_payment_fields')
    if (type(payment['amount_minor']) is not int or type(payment['limit_minor']) is not int
            or payment['amount_minor'] <= payment['limit_minor'] or payment['limit_minor'] < 0
            or type(payment['authorized']) is not bool or type(payment['sanctions_match']) is not bool):
        raise ValueError('expected_over_limit_payment')
    return {'paymentAmountMinor': payment['amount_minor'], 'paymentLimitMinor': payment['limit_minor'],
            'paymentAuthorized': payment['authorized'], 'paymentSanctions': payment['sanctions_match'],
            'paymentKey': key, 'paymentSha256': digest(payment)}


def typed(values):
    return {k: {'value': v, 'type': 'Boolean' if type(v) is bool else 'Long' if type(v) is int else 'String'}
            for k, v in values.items()}


class Approval:
    def __init__(self, directory, engine, support, payment=None, checkpoint=lambda event: None):
        self.path = Path(directory) / 'approval.json'
        self.engine, self.support, self.checkpoint = engine, support, checkpoint
        context = {'engine': engine.base, 'support': support.base, 'definition': DEFINITION}
        root = Path(__file__).resolve().parents[1]
        context['sources'] = {name: hashlib.sha256((root / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                              for name in ('services/payment_approval.py', 'workloads/payment-approval.bpmn')}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if self.data.get('version') != 1 or self.data['context'] != context:
                raise ValueError('approval_context_mismatch')
            if payment is not None and self.data['payment'] != payment:
                raise ValueError('approval_payment_mismatch')
        else:
            if payment is None:
                raise ValueError('payment_required_for_new_review')
            key = 'approval-' + uuid.uuid4().hex
            snapshot(payment, key)
            self.data = {'version': 1, 'context': context, 'payment': payment, 'key': key,
                         'review_expires_utc_s': time.time() + 3600}
            self.save()

    def save(self):
        atomic_json(self.path, self.data)

    def history(self):
        query = urllib.parse.urlencode({'processInstanceBusinessKey': self.data['key'],
                                        'processDefinitionKey': DEFINITION})
        return self.engine.request('GET', '/history/process-instance?' + query)

    def start(self):
        if not self.data.get('start_intent'):
            self.data['start_intent'] = True; self.save()
            variables = snapshot(self.data['payment'], self.data['key'])
            variables['reviewerDecision'] = 'pending'
            result = self.engine.request('POST', '/process-definition/key/' + DEFINITION + '/start',
                                         {'businessKey': self.data['key'], 'variables': typed(variables)})
            self.checkpoint('start_response')
            self.data['process_id'] = result['id']; self.save()
        if not self.data.get('process_id'):
            matches = self.history()
            if len(matches) != 1 or matches[0].get('businessKey') != self.data['key']:
                raise ValueError('unresolved_start_no_retry')
            self.data['process_id'] = matches[0]['id']; self.save()
        return self.status()

    def status(self):
        pid = self.data['process_id']
        matches = self.history()
        if len(matches) != 1 or matches[0]['id'] != pid:
            raise ValueError('nonunique_process_identity')
        history = matches[0]
        query = urllib.parse.urlencode({'processInstanceId': pid})
        # Runtime state can disappear between reads when settlement finishes.
        # Historic variables survive both that transition and driver restart.
        raw = self.engine.request('GET', '/history/variable-instance?' + query)
        values = {v['name']: v.get('value') for v in raw if not v.get('taskId')}
        expected = snapshot(self.data['payment'], self.data['key'])
        if any(values.get(k) != v or type(values.get(k)) is not type(v) for k, v in expected.items()):
            raise ValueError('payment_snapshot_changed')
        state = self.support.request('GET', '/state/' + self.data['key'])
        tasks = self.engine.request('GET', '/task?' + query)
        if not history.get('endTime'):
            if tasks:
                if len(tasks) != 1 or tasks[0]['taskDefinitionKey'] != 'payment_review':
                    raise ValueError('unexpected_review_task')
                if state.get('effect_count') != 0 or state.get('result') or state.get('violations'):
                    raise ValueError('effect_before_review')
                return {'state': 'pending', 'task_id': tasks[0]['id'], 'effect_count': 0,
                        'process_id': pid, 'key': self.data['key']}
            return {'state': 'settling', 'process_id': pid, 'key': self.data['key']}
        if history.get('state') != 'COMPLETED' or tasks:
            raise ValueError('unexpected_terminal_state')
        decision = self.data.get('decision')
        if (decision not in ('approve', 'reject') or values.get('reviewerDecision') != decision
                or values.get('reviewerIdentity') != self.data.get('reviewer')
                or values.get('reviewedPaymentSha256') != expected['paymentSha256']):
            raise ValueError('decision_audit_mismatch')
        activities = self.engine.request('GET', '/history/activity-instance?' + query)
        if sum(a['activityId'] == 'payment_review' and bool(a.get('endTime')) for a in activities) != 1:
            raise ValueError('missing_completed_review')
        if decision == 'reject':
            outcome, effects = 'review_rejected', 0
            if state.get('result'):
                raise ValueError('rejection_reached_ledger')
        else:
            p = self.data['payment']
            outcome = 'settled' if p['authorized'] and not p['sanctions_match'] else 'rejected'
            effects = int(outcome == 'settled')
            variables = typed({**p, 'scenario': 'settlement', 'approved': True,
                               'logical_request_id': self.data['key']})
            request = task_request({'variables': variables})
            fingerprint = digest({'operation': request['operation'], 'payload': request['payload']})
            if state.get('fingerprint') != fingerprint or (state.get('result') or {}).get('outcome') != outcome:
                raise ValueError('ledger_payload_or_outcome_mismatch')
        if state.get('effect_count') != effects or state.get('violations'):
            raise ValueError('ledger_effect_mismatch')
        result = {'state': 'completed', 'decision': decision, 'outcome': outcome, 'effect_count': effects,
                  'process_id': pid, 'key': self.data['key']}
        self.data['verified_result'] = result; self.save()
        return result

    def decide(self, decision, reviewer_identity='simulated-benchmark-reviewer'):
        if decision not in ('approve', 'reject'):
            raise ValueError('invalid_review_decision')
        if self.data.get('decision') not in (None, decision):
            raise ValueError('conflicting_review_decision')
        current = self.status()
        if current['state'] == 'completed':
            return current
        if self.data.get('complete_intent'):
            if current['state'] == 'pending':
                raise ValueError('unresolved_completion_no_retry')
            return current
        if time.time() >= self.data['review_expires_utc_s']:
            raise ValueError('review_expired')
        if current['state'] != 'pending':
            raise ValueError('review_not_pending')
        self.data.update(decision=decision, complete_intent=True, task_id=current['task_id'],
                         reviewer=reviewer_identity, decision_utc_s=time.time())
        self.save()
        self.engine.request('POST', '/task/' + current['task_id'] + '/complete',
                            {'variables': typed({'reviewerDecision': decision,
                                                 'reviewerIdentity': self.data['reviewer'],
                                                 'reviewedPaymentSha256': digest(self.data['payment'])})})
        self.checkpoint('complete_response')
        self.data['complete_response_received'] = True; self.save()
        return self.status()


def clients():
    auth = 'Basic ' + base64.b64encode((os.environ['BENCH_USERNAME'] + ':' + os.environ['BENCH_PASSWORD']).encode()).decode()
    return (Client(os.environ['BENCH_BASE_URL'], auth),
            Client(os.environ['BENCH_SUPPORT_URL'], 'Bearer ' + os.environ['BENCH_SUPPORT_TOKEN']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--action', choices=('start', 'status', 'approve', 'reject'), required=True)
    parser.add_argument('--payment', type=Path, help='Trusted synthetic payment JSON; required for new review')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with owner(args.output):
        payment = json.loads(args.payment.read_text()) if args.payment else None
        review = Approval(args.output, *clients(), payment)
        result = review.start() if args.action == 'start' else review.status() if args.action == 'status' else review.decide(args.action)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
