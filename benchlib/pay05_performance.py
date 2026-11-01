"""Declared PAY-05 delay profiles, paired design and independent summaries."""
from collections import Counter
import copy
from datetime import datetime
import hashlib
import math
import re
from statistics import median

from benchlib.analysis import distribution
from benchlib.pay05_load import qualification_plan as functional_plan, assess_cloud
from services.workflow_checks import fingerprint

MODE = 'pay05_delay_profiles_v1'
OUTCOMES = ('settled', 'rejected', 'blocked')


def resolve_corpus(protocol, base):
    functional_plan(base)
    if (protocol.get('protocol') != 'pay05-delay-profile-comparison-v1'
            or protocol.get('study_id') != 'PAY-05' or protocol.get('schema_version') != 1
            or protocol.get('base_corpus_fingerprint') != fingerprint(base)
            or not isinstance(protocol.get('trace_seed'), str) or not protocol['trace_seed']):
        raise ValueError('PAY-05 protocol or base corpus identity changed')
    profiles = protocol['profiles']
    if len(profiles) != 3 or len({p['id'] for p in profiles}) != 3:
        raise ValueError('Expected three distinct declared profiles')
    cases = []
    for profile in profiles:
        if (not re.fullmatch('[a-z_]{1,32}', profile['id']) or set(profile['delay_ms']) != {'a', 'b', 'c'}
                or any(type(v) is not int or not 0 <= v <= 5000 for v in profile['delay_ms'].values())):
            raise ValueError('Invalid quote-delay profile')
        for original in base['cases']:
            case = copy.deepcopy(original)
            case.update(case_id=original['case_id'] + '__' + profile['id'], base_case_id=original['case_id'],
                        profile_id=profile['id'], require_parallel_overlap=False)
            for route in ('a', 'b', 'c'): case['quotes'][route]['delay_ms'] = profile['delay_ms'][route]
            cases.append(case)
    rank = lambda c: (hashlib.sha256((protocol['trace_seed'] + ':' + c['base_case_id'] + ':' + c['profile_id']).encode()).hexdigest(), c['case_id'])
    cases.sort(key=rank)
    if len(cases) != 66 or len({c['case_id'] for c in cases}) != 66:
        raise ValueError('PAY-05 trace inventory changed')
    return {'schema_version': 1, 'study_id': 'PAY-05', 'case_mode': MODE, 'protocol_fingerprint': fingerprint(protocol),
            'base_corpus_fingerprint': fingerprint(base), 'base_corpus_revision': 2,
            'trace_seed': protocol['trace_seed'], 'profiles': copy.deepcopy(profiles), 'cases': cases,
            'scope': protocol['population'], 'overlap_scope': protocol['overlap_scope']}


def designs(protocol, corpus, run_id):
    if (not re.fullmatch('[0-9a-f]{32}', run_id) or corpus.get('case_mode') != MODE
            or corpus.get('protocol_fingerprint') != fingerprint(protocol) or len(corpus['cases']) != 66):
        raise ValueError('Invalid frozen PAY-05 design identity')
    required = {'rate_per_s': .5, 'qualification_warmup_cycles': 1, 'qualification_measurement_cycles': 1,
                'measurement_warmup_cycles': 1, 'measurement_cycles': 6, 'pairs': 5, 'deadline_s': 2,
                'poll_interval_ms': 100, 'worker_slots': 3, 'worker_idle_s': .1, 'fixed_vus': 16, 'drain_s': 30}
    if any(type(protocol[k]) not in (int, float) or protocol[k] != v for k, v in required.items()):
        raise ValueError('Protocol differs from the declared first PAY-05 comparison')
    common = {'case_mode': MODE, 'corpus_fingerprint': fingerprint(corpus), 'corpus_size': 66,
              'rate_per_s': .5, 'drain_s': 30, 'deadline_s': 2, 'poll_interval_ms': 100, 'fixed_vus': 16,
              'worker_slots': 3, 'worker_idle_s': .1, 'deadline_miss_action': 'report',
              'stop_rule': protocol['stop_rule'], 'protocol_fingerprint': fingerprint(protocol)}
    common['profile_ids'] = [p['id'] for p in corpus['profiles']]
    qualification = {**common, 'protocol': 'pay05-profile-qualification-v1', 'warmup_arrivals': 66, 'measurement_arrivals': 66,
                     'lanes': [{'arm': a, 'rate': .5} for a in ('sequential', 'parallel')],
                     'performance_comparison_complete': False, 'scope': 'Qualify the exact declared delay trace before any paired measurement'}
    seed = hashlib.sha256(('pay05-pair-order-v1:' + run_id).encode()).hexdigest()
    # Cryptographic ordering makes the retained seed reproducible across Python versions.
    ordering = sorted(range(5), key=lambda i: hashlib.sha256((seed + ':' + str(i)).encode()).hexdigest())
    sequential_first = set(ordering[:3])
    pairs = [['sequential', 'parallel'] if i in sequential_first else ['parallel', 'sequential'] for i in range(5)]
    measurement = {**common, 'protocol': 'pay05-delay-profile-comparison-v1', 'run_id': run_id,
                   'warmup_arrivals': 66, 'measurement_arrivals': 396, 'warmup_s': 132, 'measurement_s': 792,
                   'pair_order_seed': seed, 'pairs': pairs, 'primary_outcome': protocol['primary_outcome'],
                   'secondary_outcomes': protocol['secondary_outcomes'], 'population': protocol['population'],
                   'interpretation': protocol['interpretation'], 'only_changed_control': protocol['changed_factor']}
    return qualification, measurement


def strata(events, corpus, result):
    """Use only independently accepted rows, with denominators from the trace."""
    profiles = [p['id'] for p in corpus['profiles']]; answer = {}
    for phase, cohort in result['cohorts'].items():
        expected = Counter((corpus['cases'][i % 66]['profile_id'], corpus['cases'][i % 66]['expected']['outcome']) for i in range(cohort['offered']))
        invalid = {r['logical_request_id'] for r in cohort['invalid_or_unknown']}
        done = [r for r in events if r['event'] == 'completed' and r['phase'] == phase and r['logical_request_id'] not in invalid]
        answer[phase] = {}
        for profile in profiles:
            rows = [r for r in done if r['details']['original']['profile_id'] == profile]
            categories = {}
            for kind in OUTCOMES:
                selected = [r for r in rows if r['details']['original']['expected']['outcome'] == kind]
                categories[kind] = {'expected': expected[profile, kind], 'verified': len(selected),
                    'within_deadline': sum(r['latency_s'] <= 2 for r in selected),
                    'observed_latency_s': distribution([r['latency_s'] for r in selected])}
            spans, registration, overlap, replays = [], [], 0, 0
            for row in rows:
                backend = row['details']['backend']; quotes = [backend['receipts']['quote_' + r] for r in ('a', 'b', 'c')]
                spans.append((max(q['finished_ns'] for q in quotes) - min(q['started_ns'] for q in quotes)) / 1e9)
                registration.append((max(q['finished_ns'] for q in quotes) - backend['registered_ns']) / 1e9)
                overlap += min(q['finished_ns'] for q in quotes) > max(q['started_ns'] for q in quotes)
                replays += sum(a['event'] == 'replay' for a in backend['audit'])
            answer[phase][profile] = {'outcomes': categories, 'verified': len(rows),
                'all_three_quotes_overlap': overlap, 'quote_receipt_span_s': distribution(spans),
                'registration_to_all_quotes_s': distribution(registration), 'backend_replays': replays,
                'first_operation_receipts': 4 * len(rows)}
    return answer


def worker_assessment(record, execution, total):
    issues = []
    try:
        assert record['type'] == 'pay05_worker_run' and re.fullmatch('[0-9a-f]{32}', record['execution_id'])
        assert record['worker_slots'] == 3 and record['idle_s'] == .1
        start, end = (datetime.fromisoformat(record[k]) for k in ('started_at', 'finished_at'))
        assert start.tzinfo and end.tzinfo
        assert start <= datetime.fromisoformat(execution['started_at']) < datetime.fromisoformat(execution['finished_at']) <= end
        rows = record['records']; statuses = Counter(r['status'] for r in rows)
        assert not set(statuses) - {'completed', 'retry_or_incident', 'fetch_error'}
        completed = [r for r in rows if r['status'] == 'completed']
        assert len(completed) == 4 * total and len({r['task_id'] for r in completed}) == 4 * total
        assert Counter(r['operation'] for r in completed) == Counter({k: total for k in ('quote_a', 'quote_b', 'quote_c', 'select')})
    except (AssertionError, KeyError, TypeError, ValueError):
        issues.append('worker_attempt_inventory_incomplete_or_unbound'); statuses = Counter()
    return {'status': 'passed' if not issues else 'failed', 'issues': issues, 'statuses': dict(statuses),
            'scope': 'Worker task attempts and fetch failures; backend replays are counted separately. Not every network packet or provider retry.'}


def assess_profile_lane(events, summary, execution, calibration, final, telemetry, reset, corpus, worker):
    result = assess_cloud(events, summary, execution, calibration, final, telemetry, reset, corpus)
    worker_result = worker_assessment(worker, execution, sum(c['offered'] for c in result['cohorts'].values()))
    issues = result['issues'] + worker_result['issues']
    return {**result, 'status': 'passed' if not issues else 'failed', 'issues': issues, 'case_mode': MODE,
            'profile_outcomes': strata(events, corpus, result), 'worker_attempts': worker_result,
            'scope': 'One arm of the prespecified synthetic PAY-05 delay-profile study; paired conclusions require the complete design and final independent audit.'}


def qualified_gate(results, plan):
    if len(results) != 2 or len(plan['lanes']) != 2:
        raise ValueError('Both profile qualification lanes are required')
    for result, lane in zip(results, plan['lanes']):
        if (result['status'] != 'passed' or result['issues'] or result['arm'] != lane['arm'] or result['offered_rate'] != .5
                or result.get('case_mode') != MODE or result.get('worker_attempts', {}).get('status') != 'passed'):
            raise ValueError('Profile qualification did not pass the declared gates')
        for phase, count in [('warmup', 66), ('measurement', 66)]:
            c = result['cohorts'][phase]
            if c['offered'] != count or c['correct'] != count or c['dropped'] or c['invalid_or_unknown']:
                raise ValueError('Profile qualification accounting failed')
    return True


def summarize_pairs(plan, records):
    if (plan.get('protocol') != 'pay05-delay-profile-comparison-v1' or len(plan['pairs']) != 5
            or any(sorted(p) != ['parallel', 'sequential'] for p in plan['pairs']) or plan['rate_per_s'] != .5
            or plan['measurement_arrivals'] != 396 or plan['measurement_s'] != 792):
        raise ValueError('Expected the declared five-pair PAY-05 design')
    expected = [(f'p{i}-{j}-{arm}', i, arm) for i, pair in enumerate(plan['pairs'], 1) for j, arm in enumerate(pair, 1)]
    if len(records) > 10 or len({r['folder'] for r in records}) != len(records):
        raise ValueError('Duplicate or extra performance arms')
    arms = []
    for record, (name, pair, arm) in zip(records, expected):
        if record['folder'] != name or record['arm'] != arm or record['offered_rate'] != .5 or record.get('case_mode') != MODE:
            raise ValueError('Performance arms differ from the frozen order')
        m = record['cohorts']['measurement']; w = record['cohorts']['warmup']
        if m['offered'] != 396 or w['offered'] != 66:
            raise ValueError('Performance cohort length changed')
        for kind, n in [('settled', 306), ('rejected', 18), ('blocked', 72)]:
            row = m['outcomes'][kind]
            if row['expected'] != n or not 0 <= row['within_deadline'] <= row['verified'] <= n:
                raise ValueError('Performance outcome denominator changed')
            if row['observed_latency_s']['n'] != row['verified']:
                raise ValueError('Performance latency count changed')
            if any(row['observed_latency_s'][k] is not None and (not math.isfinite(row['observed_latency_s'][k]) or row['observed_latency_s'][k] < 0) for k in ('p95', 'p99')):
                raise ValueError('Invalid performance latency')
        if m['correct'] != sum(v['verified'] for v in m['outcomes'].values()) or m['on_time_correct'] != sum(v['within_deadline'] for v in m['outcomes'].values()):
            raise ValueError('Performance outcomes do not reconcile')
        if record['status'] == 'passed' and (record['issues'] or m['correct'] != 396 or w['correct'] != 66 or m['dropped'] or w['dropped'] or m['invalid_or_unknown'] or w['invalid_or_unknown']):
            raise ValueError('Invalid performance arm labeled passed')
        if record['status'] == 'passed' and record.get('worker_attempts', {}).get('status') != 'passed':
            raise ValueError('Missing valid worker evidence')
        for phase, cohort, cycles in [('warmup', w, 1), ('measurement', m, 6)]:
            profiles = record['profile_outcomes'][phase]
            if set(profiles) != set(plan['profile_ids']) or len(profiles) != 3:
                raise ValueError('Missing or extra delay profiles')
            for profile in profiles.values():
                if set(profile['outcomes']) != set(OUTCOMES): raise ValueError('Missing outcome stratum')
                for kind, per_cycle in [('settled', 17), ('rejected', 1), ('blocked', 4)]:
                    row = profile['outcomes'][kind]
                    if (row['expected'] != cycles * per_cycle or not 0 <= row['within_deadline'] <= row['verified'] <= row['expected']
                            or row['observed_latency_s']['n'] != row['verified']
                            or any(row['observed_latency_s'][k] is not None and (not math.isfinite(row['observed_latency_s'][k]) or row['observed_latency_s'][k] < 0) for k in ('p95', 'p99'))
                            or (record['status'] == 'passed' and (row['verified'] != row['expected'] or row['observed_latency_s']['p95'] is None))):
                        raise ValueError('Invalid profile denominator or timing')
                if profile['verified'] != sum(v['verified'] for v in profile['outcomes'].values()):
                    raise ValueError('Profile verification count does not reconcile')
            for kind in OUTCOMES:
                if any(sum(p['outcomes'][kind][key] for p in profiles.values()) != cohort['outcomes'][kind][key] for key in ('expected', 'verified', 'within_deadline')):
                    raise ValueError('Profile outcomes do not reconcile')
        arms.append({'folder': name, 'pair': pair, 'arm': arm, 'status': record['status'], 'issues': record['issues'],
                     'measurement': m, 'profiles': record['profile_outcomes']['measurement'],
                     'on_time_settlements_per_s': m['outcomes']['settled']['within_deadline'] / 792})
    complete = len(arms) == 10 and all(r['status'] == 'passed' for r in arms)
    result = {'status': 'complete' if complete else 'incomplete_or_invalid', 'completed_arms': len(arms), 'planned_arms': 10,
              'performance_comparison_complete': complete, 'primary_outcome': plan['primary_outcome'],
              'rate_per_s': .5, 'measurement_s_per_arm': 792, 'arms': arms, 'comparison': None,
              'limits': [plan['population'], plan['interpretation'], 'Completion includes 100 ms polling; warmup is separate.']}
    if not complete: return result
    pairs = []
    for pair in range(1, 6):
        a, b = ({r['arm']: r for r in arms if r['pair'] == pair}[arm] for arm in ('sequential', 'parallel'))
        categories = {}
        for kind in OUTCOMES:
            left, right = a['measurement']['outcomes'][kind], b['measurement']['outcomes'][kind]
            categories[kind] = {'sequential_within_deadline': left['within_deadline'], 'parallel_within_deadline': right['within_deadline'],
                'sequential_p95_ms': 1000 * left['observed_latency_s']['p95'], 'parallel_p95_ms': 1000 * right['observed_latency_s']['p95']}
        profiles = {}
        for profile in a['profiles']:
            left, right = a['profiles'][profile]['outcomes'], b['profiles'][profile]['outcomes']
            profiles[profile] = {kind: {'sequential_within_deadline': left[kind]['within_deadline'], 'parallel_within_deadline': right[kind]['within_deadline'],
                 'sequential_p95_ms': 1000 * left[kind]['observed_latency_s']['p95'], 'parallel_p95_ms': 1000 * right[kind]['observed_latency_s']['p95']} for kind in OUTCOMES}
        pairs.append({'pair': pair, 'sequential_on_time_settlements_per_s': a['on_time_settlements_per_s'],
                      'parallel_on_time_settlements_per_s': b['on_time_settlements_per_s'],
                      'parallel_change_in_timely_settlements_per_s': b['on_time_settlements_per_s'] - a['on_time_settlements_per_s'],
                      'outcomes': categories, 'profiles': profiles})
    delta = [p['parallel_change_in_timely_settlements_per_s'] for p in pairs]
    result['comparison'] = {'pairs': pairs, 'median_paired_change_in_timely_settlements_per_s': median(delta),
        'paired_change_range_per_s': [min(delta), max(delta)], 'measured_workflows': 3960,
        'outcomes': {arm: {kind: {k: sum(r['measurement']['outcomes'][kind][k] for r in arms if r['arm'] == arm)
                    for k in ('expected', 'verified', 'within_deadline')} for kind in OUTCOMES} for arm in ('sequential', 'parallel')}}
    return result
