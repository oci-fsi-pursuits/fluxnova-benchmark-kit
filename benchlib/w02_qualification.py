"""Independent checks of W02 load-driver evidence; no cloud performance verdict."""
from collections import Counter, defaultdict
import math

from benchlib.analysis import distribution, percentile
from scripts.run_w02_functional import require_outcome
from benchlib.workflow_corpus import mixed_case
from services.workflow_checks import fingerprint


def expected_case(index, phase, corpus=None):
    """Reconstruct the declared approved trace independently of the JS driver."""
    if corpus is not None:
        return mixed_case(index,phase,corpus)
    return {'case_id':f'approved-{phase}-{index}', 'payment':{
        'payment_id':f'synthetic-{phase}-{index}', 'beneficiary':'synthetic-beneficiary',
        'amount_minor':125,'limit_minor':1000,'authorized':True,'sanctions_match':False,
        'high_risk':False,'stale_market':False},
        'schedule':{'kyc':{'delay_ms':400,'fault':'none'},'risk':{'delay_ms':600,'fault':'none'}},
        'expected_outcome':'settled','expected_checks':{'kyc':'approved','risk':'accepted'}}


def audit(events, summary, returncode, calibration, final_state, *, corpus=None, require_deadlines=True):
    """Audit accounting and observation clocks, without asserting a host scope."""
    settings = summary['w02']
    issues = []
    mixed = settings.get('case_mode') == 'mixed_sensitivity'
    if mixed and corpus is None:
        issues.append('mixed_corpus_required')
    if corpus is not None and (not mixed or settings.get('corpus_fingerprint') != fingerprint(corpus) or
                               settings.get('corpus_size') != len(corpus['cases'])):
        issues.append('mixed_corpus_identity_mismatch')
    cohorts = {}
    for phase in ('warmup', 'measurement'):
        rows = [event for event in events if event.get('phase') == phase]
        by_id = defaultdict(list)
        for row in rows:
            by_id[row['logical_request_id']].append(row)
        expected = int(settings['rate'] * (settings['duration_s'] if phase=='measurement' else settings.get('warmup_s',0)))
        phase_summary = settings[phase]
        if phase_summary['scheduled'] != expected:
            issues.append(phase+'_scheduled_count_differs_from_plan')
        wanted_ids = {f'{settings.get("run_id")}:{phase}:{i}' for i in range(int(expected))}
        if set(by_id) != wanted_ids:
            issues.append(phase + '_arrival_identity_mismatch')
        counters = Counter(row['event'] for row in rows)
        correct, invalid = [], []
        for identity, records in by_id.items():
            kinds = Counter(record['event'] for record in records)
            if any(kinds[k] != 1 for k in ('scheduled', 'started', 'accepted', 'completed')) or kinds['outcome'] or kinds['error']:
                invalid.append({'logical_request_id':identity, 'reason':'incomplete_or_duplicate_lifecycle'})
                continue
            done = next(r for r in records if r['event'] == 'completed')
            try:
                data = done['details']
                index = int(identity.rsplit(':',1)[1])
                if data['original'] != expected_case(index,phase,corpus):
                    raise AssertionError('original_input_differs_from_declared_trace')
                if done['status'] != 'correct' or data.get('safety_violation'):
                    raise AssertionError('driver_did_not_verify_original_outcome')
                require_outcome(data['original'], data['backend'], data['history'], data['activities'])
                instance = data['history']['instance']
                if instance['businessKey'] != identity or instance['state'] != 'COMPLETED' or instance['id'] != data['process_instance_id']:
                    raise AssertionError('history_identity_mismatch')
                if data['backend']['case_key'] != identity or data['backend']['process_id'] != instance['id']:
                    raise AssertionError('backend_identity_mismatch')
                for op, receipt in data['backend']['receipts'].items():
                    if receipt['case_key'] != identity or receipt['operation'] != op:
                        raise AssertionError('receipt_identity_mismatch')
                    if (type(receipt['started_ns']) is not int or type(receipt['finished_ns']) is not int or
                            not 0 <= receipt['started_ns'] <= receipt['finished_ns']):
                        raise AssertionError('invalid_receipt_clock')
                planned = next(r for r in records if r['event'] == 'scheduled')['elapsed_s']
                if abs(planned - (index/settings['rate'] - (settings['warmup_s'] if phase=='warmup' else 0))) > .001:
                    raise AssertionError('arrival_schedule_mismatch')
                if not math.isfinite(done['latency_s']) or done['latency_s'] < 0 or abs(done['elapsed_s'] - planned - done['latency_s']) > .001:
                    raise AssertionError('invalid_observation_clock')
                clocks = [next(r for r in records if r['event']==kind)['elapsed_s']
                          for kind in ('scheduled','started','accepted','completed')]
                if not all(math.isfinite(t) for t in clocks) or any(b<a-.001 for a,b in zip(clocks,clocks[1:])):
                    raise AssertionError('invalid_lifecycle_clock')
                if ('observed_complete_elapsed_s' in data and
                        abs(data['observed_complete_elapsed_s']-done['elapsed_s'])>.001):
                    raise AssertionError('observation_clock_fields_disagree')
                kyc, risk = (data['backend']['receipts'][op] for op in ('kyc', 'risk'))
                if settings['arm'] == 'sequential' and risk['started_ns'] < kyc['finished_ns']:
                    raise AssertionError('sequential_checks_overlap')
                correct.append(done)
            except (AssertionError, KeyError, TypeError, ValueError) as error:
                invalid.append({'logical_request_id':identity, 'reason':str(error) if isinstance(error, AssertionError) else type(error).__name__})
        lag = [max(0,r['elapsed_s'] - (int(r['logical_request_id'].rsplit(':',1)[1])/settings['rate'] -
                (settings['warmup_s'] if phase=='warmup' else 0))) for r in rows if r['event'] == 'started']
        if len(correct) != expected or phase_summary['started'] != expected or len(by_id) != expected or phase_summary['dropped'] != 0:
            issues.append(phase + '_counts_or_outcomes_failed')
        if expected and (percentile(lag, .99) is None or percentile(lag, .99) > .1):
            issues.append(phase + '_arrival_lag_exceeds_100ms')
        cohorts[phase] = {'offered':expected, 'started':counters['started'], 'accepted':counters['accepted'],
                         'completed':counters['completed'], 'correct':len(correct), 'dropped':phase_summary['dropped'],
                         'invalid_or_unknown':invalid, 'arrival_lag_s':distribution(lag),
                         'observed_latency_s':distribution([r['latency_s'] for r in correct]),
                         'on_time_correct':sum(r['latency_s'] <= settings['deadline_s'] for r in correct)}
        expected_outcomes = Counter(expected_case(i,phase,corpus)['expected_outcome'] for i in range(expected))
        verified = {outcome:[r for r in correct if r['details']['original']['expected_outcome']==outcome]
                    for outcome in ('settled','rejected','blocked')}
        cohorts[phase]['outcomes'] = {outcome:{'expected':expected_outcomes[outcome],
            'verified':len(rows),'within_deadline':sum(r['latency_s']<=settings['deadline_s'] for r in rows),
            'observed_latency_s':distribution([r['latency_s'] for r in rows])} for outcome,rows in verified.items()}
        cohort_duration = settings['duration_s'] if phase=='measurement' else settings.get('warmup_s',0)
        cohorts[phase]['on_time_settlements_per_s'] = (cohorts[phase]['outcomes']['settled']['within_deadline']/cohort_duration
                                                     if cohort_duration else None)
        cohorts[phase]['unresolved_or_invalid_count'] = expected-len(correct)
    measured = [event for event in events if event.get('phase') == 'measurement']
    starts = {r['logical_request_id']:r['elapsed_s'] for r in measured if r['event'] == 'started'}
    ends = {r['logical_request_id']:r['elapsed_s'] for r in measured if r['event'] == 'completed'}
    end = settings['duration_s']
    samples = [{'elapsed_s':t, 'outstanding':sum(start <= t and ends.get(key, math.inf) > t for key,start in starts.items())}
               for t in range(end + 1)]
    early = [s['outstanding'] for s in samples if end * .25 <= s['elapsed_s'] < end * .5]
    late = [s['outstanding'] for s in samples if end * .75 <= s['elapsed_s'] < end]
    bounded = bool(early and late) and max(s['outstanding'] for s in samples) <= 4 and sum(late)/len(late) <= sum(early)/len(early)+1
    if not bounded:
        issues.append('observed_backlog_guard_failed')
    if require_deadlines and cohorts['measurement']['on_time_correct'] != cohorts['measurement']['offered']:
        issues.append('measurement_deadline_miss')
    total = sum(c['offered'] for c in cohorts.values())
    expected_effects = sum(c['outcomes']['settled']['expected'] for c in cohorts.values())
    if final_state != {'active_instances':0,'external_tasks':0,'incidents':0,'completed_instances':total,'effects':expected_effects,'registrations':total,'receipts':3*total}:
        issues.append('final_engine_backend_reconciliation_failed')
    metrics = summary.get('metrics', {})
    http_rate = metrics.get('http_reqs', {}).get('values', {}).get('rate')
    headroom = (calibration.get('valid') is True and isinstance(http_rate, (int,float)) and
                2 * http_rate <= calibration.get('requested_http_rate',0))
    if not headroom:
        issues.append('loopback_calibration_does_not_cover_observed_http_volume')
    if returncode != 0:
        issues.append('load_process_failed')
    return {'status':'passed' if not issues else 'failed', 'issues':issues, 'arm':settings['arm'],
            'offered_rate':settings['rate'], 'cohorts':cohorts,
            'case_mode':settings.get('case_mode','approved'),'deadline_gate_required':require_deadlines,
            'outcome_counting':'Correct settlements, expected refusals and safe unknown-check stops are separate. Only settlements contribute to settlement goodput.',
            'observed_backlog':{'samples':samples,'bounded_under_declared_guard':bounded,
                                'limit':'Short local check, not proof of sustainable capacity'},
            'final_state':final_state, 'loopback_http_headroom_passed':headroom}


def assess(events, summary, returncode, calibration, final_state, *, corpus=None, require_deadlines=True):
    return {**audit(events, summary, returncode, calibration, final_state,corpus=corpus,require_deadlines=require_deadlines),
            'scope':'LOCAL LOAD-DRIVER QUALIFICATION - NOT OCI PERFORMANCE',
            'cloud_generator_headroom_validated':False, 'cloud_telemetry_complete':False,
            'performance_claim':None, 'cloud_resources_accessed':False}
