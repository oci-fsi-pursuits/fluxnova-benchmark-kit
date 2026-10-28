#!/usr/bin/env python3
"""At most two native OCI chat requests with a synthetic tool-result fixture.

Does not start payment workflows or provision AI infrastructure. Retains failed
responses and server usage. The terminal NONE policy constrains the control flow.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.oci_model import OCIGenericModel, request_timeout, conversation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--compartment-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--auth', choices=('security_token', 'instance_principal'), default='security_token')
    parser.add_argument('--profile', default='BENCHMARK')
    parser.add_argument('--terminal-tool-policy', choices=('NONE', 'AUTO'), default='NONE')
    parser.add_argument('--read-timeout-s', type=float, default=20)
    parser.add_argument('--wall-s', type=float, default=60)
    parser.add_argument('--untrusted-context-file', type=Path)
    args = parser.parse_args()
    if not 0 < args.wall_s <= 120:parser.error('wall-s must be positive and at most 120')
    try:request_timeout(1,args.read_timeout_s)
    except ValueError:parser.error('read-timeout-s must be positive, finite and at most 60')
    note=args.untrusted_context_file.read_text(encoding='utf-8') if args.untrusted_context_file else None
    conversation([],[],note)
    args.output.mkdir(parents=True, exist_ok=False)
    import oci
    if args.auth == 'instance_principal':
        config = {'region': args.region}
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    else:
        config = oci.config.from_file(profile_name=args.profile)
        signer = oci.auth.signers.SecurityTokenSigner(
            Path(os.path.expanduser(config['security_token_file'])).read_text().strip(),
            oci.signer.load_private_key_from_file(os.path.expanduser(config['key_file']), config.get('pass_phrase')))
    records, prior = [], []
    def record(value):
        records.append(value)
        (args.output / ('request-' + str(len(records)) + '.json')).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    adapter = OCIGenericModel(config, signer, args.compartment_id, args.model_id, args.region,
                             prior_responses=lambda: prior, record=record, max_requests=2,
                             terminal_tool_policy=args.terminal_tool_policy, read_timeout_s=args.read_timeout_s, untrusted_context=note)
    result = {'passed': False, 'region': args.region, 'model_id': args.model_id, 'auth': args.auth,
              'terminal_tool_policy': args.terminal_tool_policy, 'synthetic_tool_result': True,
              'workflow_starts': 0, 'request_budget': 2, 'max_output_tokens_per_request': 256}
    result.update(read_timeout_s=args.read_timeout_s,wall_s=args.wall_s,
                  untrusted_context_sha256=hashlib.sha256(note.encode()).hexdigest() if note is not None else None)
    deadline = time.monotonic() + args.wall_s
    try:
        first = adapter({'turn': 0, 'tool_results': []}, deadline)
        if time.monotonic() >= deadline:raise ValueError('wall_budget')
        if first['final'] or len(first['tool_calls']) != 1:
            raise ValueError('screening_call_required')
        call = first['tool_calls'][0]
        if (call['name'] != 'screen_beneficiary' or not isinstance(call['id'], str) or not call['id'] or
                call['arguments'] != {'beneficiary': 'synthetic-beneficiary'}):
            raise ValueError('invalid_screening_call')
        prior.append(first)
        second = adapter({'turn': 1, 'tool_results': [{'tool_call_id': call['id'], 'outcome': 'approved'}]}, deadline)
        if time.monotonic() >= deadline:raise ValueError('wall_budget')
        if second != {'final': True, 'tool_calls': []}:
            raise ValueError('unexpected_continuation_tool')
        result['passed'] = True
    except Exception as error:
        result['error_type'] = type(error).__name__
    complete=bool(records) and all(x.get('usage_complete') for x in records)
    totals={k:sum(x['server_usage'][k] for x in records if x.get('server_usage')) for k in ('prompt_tokens','completion_tokens','total_tokens')}
    result.update(requests=len(records),usage_complete=complete,known_server_usage_totals=totals,
                  server_usage_totals=totals if complete else None,unknown_usage_requests=sum(not x.get('usage_complete',False) for x in records),
                  transport_send_attempts=sum(x.get('transport_send_attempts',0) for x in records),
                  transport_resends_blocked=sum(x.get('transport_resends_blocked',0) for x in records))
    result['receipt_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob('request-*.json')}
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
