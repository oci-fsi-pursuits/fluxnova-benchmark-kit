"""Independent PAY-05 arrival accounting and qualification gates."""
from collections import Counter, defaultdict
import math

from benchlib.analysis import distribution, percentile
from benchlib.w02_cloud import assess_telemetry
from scripts.audit_pay05_functional import check_outcome, resolved_case
from services.workflow_checks import fingerprint

LANES = [{'arm': arm, 'rate': rate} for rate, arms in
         ((.5, ('sequential', 'parallel')), (1, ('parallel', 'sequential'))) for arm in arms]


def qualification_plan(corpus):
    if (corpus.get('study_id') != 'PAY-05' or corpus.get('corpus_revision') != 2
            or len(corpus['cases']) != 22 or len({c['case_id'] for c in corpus['cases']}) != 22):
        raise ValueError('Expected the qualified revision-2 PAY-05 corpus')
    return {'protocol': 'pay05-load-qualification-v1', 'corpus_fingerprint': fingerprint(corpus),
            'corpus_size': 22, 'lanes': [dict(row) for row in LANES],
            'warmup_arrivals': 22, 'measurement_arrivals': 44, 'drain_s': 30,
            'deadline_s': 2, 'poll_interval_ms': 100, 'fixed_vus': 16, 'worker_slots': 3,
            'deadline_miss_action': 'report', 'performance_comparison_complete': False,
            'stop_rule': 'Stop on invalid accounting, outcomes, generator, telemetry, controls or reset; retain every attempt.',
            'scope': 'Qualify all fixed functional scenarios under offered load. The long overlap probe is not a representative performance population.'}


def expected_case(corpus, index, key):
    return resolved_case(corpus['cases'][index % len(corpus['cases'])], key)


def audit(events, summary, returncode, calibration, final_state, corpus):
    settings, issues, cohorts = summary['pay05'], [], {}
    assert settings['arm'] in ('sequential', 'parallel') and settings['rate'] in (.5, 1)
    assert settings['deadline_s'] == 2 and settings['poll_interval_ms'] == 100
    assert settings['duration_s'] > 0 and settings['warmup_s'] >= 0
    mode = corpus.get('case_mode', 'pay05_fixed_functional')
    if (mode not in ('pay05_fixed_functional', 'pay05_delay_profiles_v1') or settings['case_mode'] != mode or settings['corpus_fingerprint'] != fingerprint(corpus)
            or settings['corpus_size'] != len(corpus['cases'])):
        issues.append('corpus_identity_mismatch')
    if any(e.get('run_id') != settings['run_id'] or e.get('phase') not in ('warmup', 'measurement') for e in events):
        issues.append('event_run_or_phase_mismatch')
    for phase in ('warmup', 'measurement'):
        seconds = settings['warmup_s'] if phase == 'warmup' else settings['duration_s']
        assert settings['rate'] * seconds == int(settings['rate'] * seconds)
        expected = int(settings['rate'] * seconds)
        rows = [e for e in events if e.get('phase') == phase]
        by_id = defaultdict(list)
        for e in rows: by_id[e['logical_request_id']].append(e)
        wanted = {f"{settings['run_id']}:{phase}:{i}" for i in range(expected)}
        if set(by_id) != wanted: issues.append(phase + '_arrival_identity_mismatch')
        if settings[phase]['scheduled'] != expected: issues.append(phase + '_scheduled_count_mismatch')
        correct, invalid, lag = [], [], []
        for key, records in by_id.items():
            try:
                kinds = Counter(r['event'] for r in records)
                assert kinds == Counter({'scheduled': 1, 'started': 1, 'accepted': 1, 'completed': 1}), 'incomplete_or_duplicate_lifecycle'
                by_kind = {r['event']: r for r in records}; done = by_kind['completed']; data = done['details']
                index = int(key.rsplit(':', 1)[1]); case = expected_case(corpus, index, key)
                assert key in wanted and data['original'] == case, 'input_differs_from_frozen_trace'
                assert done['status'] == 'correct' and not data.get('safety_violation'), 'driver_outcome_not_correct'
                pid = data['process_instance_id']; history = data['history']['instance']
                assert history['businessKey'] == key and history['state'] == 'COMPLETED', 'history_binding_mismatch'
                assert history['processDefinitionId'] == settings['definition_id'], 'wrong_process_definition'
                check_outcome(case, settings['arm'], key, pid, data['history'], data['activities'], data['backend'])
                scheduled = index / settings['rate'] - (settings['warmup_s'] if phase == 'warmup' else 0)
                clocks = [by_kind[k]['elapsed_s'] for k in ('scheduled', 'started', 'accepted', 'completed')]
                assert all(type(t) in (int, float) and math.isfinite(t) for t in clocks), 'invalid_clock'
                assert abs(clocks[0] - scheduled) <= .001 and all(b >= a - .001 for a, b in zip(clocks, clocks[1:])), 'invalid_lifecycle_order'
                assert math.isfinite(done['latency_s']) and done['latency_s'] >= 0, 'invalid_latency'
                assert abs(done['latency_s'] - (clocks[-1] - scheduled)) <= .001, 'latency_not_from_scheduled_arrival'
                assert abs(data['observed_complete_elapsed_s'] - clocks[-1]) <= .001, 'completion_clock_mismatch'
                assert done['at'] == data['observed_complete_at'] and data['poll_interval_ms'] == 100, 'observation_definition_changed'
                receipts = data['backend']['receipts']
                for route in ('a', 'b', 'c'):
                    receipt = receipts['quote_' + route]
                    assert (receipt['finished_ns'] - receipt['started_ns']) / 1e6 >= case['quotes'][route]['delay_ms'] - 1, 'declared_quote_delay_not_observed'
                backend_duration = (receipts['select']['finished_ns'] - data['backend']['registered_ns']) / 1e9
                assert backend_duration <= done['latency_s'] + .05, 'backend_span_exceeds_observed_completion'
                correct.append(done)
            except (AssertionError, KeyError, TypeError, ValueError) as error:
                invalid.append({'logical_request_id': key, 'reason': str(error) if isinstance(error, AssertionError) else type(error).__name__})
        for e in rows:
            if e['event'] == 'started':
                try:
                    t = int(e['logical_request_id'].rsplit(':', 1)[1]) / settings['rate'] - (settings['warmup_s'] if phase == 'warmup' else 0)
                    value = e['elapsed_s'] - t
                    assert math.isfinite(value) and value >= -.001
                    lag.append(max(0, value))
                except (AssertionError, KeyError, TypeError, ValueError): issues.append(phase + '_invalid_arrival_clock')
        counters = Counter(e['event'] for e in rows)
        if (len(correct) != expected or settings[phase]['started'] != expected
                or settings[phase]['dropped'] != 0 or len(by_id) != expected):
            issues.append(phase + '_counts_or_outcomes_failed')
        if expected and (len(lag) != expected or percentile(lag, .99) > .1):
            issues.append(phase + '_arrival_lag_exceeds_100ms')
        outcomes = {}
        for kind in ('settled', 'rejected', 'blocked'):
            expected_count = sum(expected_case(corpus, i, 'count')['expected']['outcome'] == kind for i in range(expected))
            verified = [r for r in correct if r['details']['original']['expected']['outcome'] == kind]
            outcomes[kind] = {'expected': expected_count, 'verified': len(verified),
                'within_deadline': sum(r['latency_s'] <= 2 for r in verified),
                'observed_latency_s': distribution([r['latency_s'] for r in verified])}
        cohorts[phase] = {'offered': expected, 'started': counters['started'], 'accepted': counters['accepted'],
            'completed': counters['completed'], 'correct': len(correct), 'dropped': settings[phase]['dropped'],
            'invalid_or_unknown': invalid, 'arrival_lag_s': distribution(lag), 'outcomes': outcomes,
            'on_time_correct': sum(r['latency_s'] <= 2 for r in correct), 'observed_latency_s': distribution([r['latency_s'] for r in correct])}
    measured = [r for r in events if r.get('phase') == 'measurement']
    starts = {r['logical_request_id']: r['elapsed_s'] for r in measured if r['event'] == 'started'}
    ends = {r['logical_request_id']: r['elapsed_s'] for r in measured if r['event'] == 'completed'}
    duration = settings['duration_s']
    samples = [{'elapsed_s': t, 'outstanding': sum(start <= t and ends.get(key, math.inf) > t for key, start in starts.items())} for t in range(int(duration) + 1)]
    early = [s['outstanding'] for s in samples if duration * .25 <= s['elapsed_s'] < duration * .5]
    late = [s['outstanding'] for s in samples if duration * .75 <= s['elapsed_s'] < duration]
    bounded = bool(early and late) and max(s['outstanding'] for s in samples) <= 4 and sum(late) / len(late) <= sum(early) / len(early) + 1
    if not bounded: issues.append('observed_backlog_guard_failed')
    total = sum(c['offered'] for c in cohorts.values()); effects = sum(c['outcomes']['settled']['expected'] for c in cohorts.values())
    expected_final = {'active_instances': 0, 'external_tasks': 0, 'incidents': 0, 'completed_instances': total,
                      'effects': effects, 'registrations': total, 'receipts': 4 * total}
    if final_state != expected_final: issues.append('final_engine_backend_reconciliation_failed')
    http_rate = summary.get('metrics', {}).get('http_reqs', {}).get('values', {}).get('rate')
    headroom = calibration.get('valid') is True and type(http_rate) in (int, float) and math.isfinite(http_rate) and 0 <= 2 * http_rate <= calibration.get('requested_http_rate', 0)
    if not headroom: issues.append('generator_calibration_does_not_cover_http_volume')
    if returncode != 0: issues.append('load_process_failed')
    return {'status': 'passed' if not issues else 'failed', 'issues': issues, 'arm': settings['arm'], 'offered_rate': settings['rate'],
            'cohorts': cohorts, 'final_state': final_state, 'observed_backlog': {'samples': samples, 'bounded_under_declared_guard': bounded},
            'loopback_http_headroom_passed': headroom, 'performance_comparison_complete': False,
            'deadline_miss_action': 'report; correctness and generator validity remain mandatory'}


def assess_cloud(events, summary, execution, calibration, final_state, telemetry, reset, corpus):
    result = audit(events, summary, execution['returncode'], calibration, final_state, corpus)
    resource = assess_telemetry(telemetry, execution)
    issues = result['issues'] + resource['issues']
    if (reset.get('verified') is not True or reset.get('verified_before_resuming_writers') is not True or not reset.get('expected_fingerprint')
            or reset.get('restored_fingerprint') != reset['expected_fingerprint']):
        issues.append('database_and_ledger_reset_not_verified')
    return {**result, 'status': 'passed' if not issues else 'failed', 'issues': issues,
            'telemetry': resource, 'checkpoint_reset': reset, 'cloud_resources_accessed': True,
            'scope': 'OCI PAY-05 fixed-corpus load qualification; not a performance comparison'}
