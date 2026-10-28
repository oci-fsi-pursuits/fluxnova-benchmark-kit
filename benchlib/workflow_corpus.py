"""Prespecified W02 dependency sensitivity grid; no customer-frequency claims."""
import copy
import random
import re

from services.workflow_checks import fingerprint, validate_case


def resolve_sensitivity(protocol, base):
    """Cross every qualified case with every delay profile, then freeze order."""
    def require(value, message):
        if not value:
            raise ValueError(message)

    identifier = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
    require(isinstance(protocol, dict) and protocol.get('schema_version') == '1.0', 'invalid_protocol')
    require(isinstance(protocol.get('study_id'), str) and identifier.fullmatch(protocol['study_id']), 'invalid_study_id')
    require(isinstance(protocol.get('title'), str) and 1 <= len(protocol['title']) <= 120, 'invalid_title')
    require(type(protocol.get('seed')) is int and 0 <= protocol['seed'] < 2**32, 'invalid_seed')
    require(isinstance(base, dict) and isinstance(base.get('cases'), list) and base['cases'], 'invalid_base_corpus')
    cases = base['cases']
    ids = [case.get('case_id') for case in cases]
    require(all(isinstance(i, str) and identifier.fullmatch(i) for i in ids), 'invalid_case_id')
    require(len(set(ids)) == len(ids), 'duplicate_case_id')
    require(ids == protocol.get('base_case_ids'), 'base_case_inventory_changed')
    for case in cases:
        validate_case(case)
        require(case.get('expected_outcome') in ('settled', 'rejected', 'blocked'), 'invalid_expected_outcome')
        checks = case.get('expected_checks', {})
        require(set(checks) == {'kyc', 'risk'}, 'invalid_expected_checks')
        require(checks['kyc'] in ('approved', 'rejected', 'review', 'unknown') and
                checks['risk'] in ('accepted', 'rejected', 'unknown'), 'invalid_expected_checks')
    require(any(c['expected_outcome'] == 'settled' for c in cases), 'settled_case_required_for_replay_probe')
    profiles = protocol.get('profiles')
    require(isinstance(profiles, list) and 1 <= len(profiles) <= 20 and len(profiles)*len(cases) <= 200, 'invalid_profile_count')
    profile_ids = []
    for profile in profiles:
        require(isinstance(profile, dict) and isinstance(profile.get('id'), str) and
                identifier.fullmatch(profile['id']), 'invalid_profile_id')
        require(isinstance(profile.get('label'), str) and 1 <= len(profile['label']) <= 100, 'invalid_profile_label')
        require(all(type(profile.get(op+'_ms')) is int and 0 <= profile[op+'_ms'] <= 5000 for op in ('kyc','risk')), 'invalid_profile_delay')
        profile_ids.append(profile['id'])
    require(len(set(profile_ids)) == len(profile_ids), 'duplicate_profile_id')
    resolved = []
    for profile in profiles:
        for source in cases:
            case = copy.deepcopy(source)
            case['source_case_id'] = source['case_id']
            case['dependency_profile'] = profile['id']
            case['case_id'] = profile['id']+'__'+source['case_id']
            require(len(case['case_id']) <= 100, 'resolved_case_id_too_long')
            case['payment']['payment_id'] = 'synthetic-'+case['case_id']
            for op in ('kyc', 'risk'):
                case['schedule'][op]['delay_ms'] = profile[op+'_ms']
            # Fast checks can finish before the second worker picks up its task.
            # Require overlap only for the prespecified long, fault-free probes.
            case['require_parallel_overlap'] = (source['case_id'] in ('approve','concurrent_completion') and
                min(profile['kyc_ms'],profile['risk_ms']) >= 400)
            validate_case(case)
            resolved.append(case)
    rng = random.Random(protocol['seed'])
    rng.shuffle(resolved)
    orders = [['sequential','parallel'] if i % 2 == 0 else ['parallel','sequential'] for i in range(len(resolved))]
    rng.shuffle(orders)
    return {
        'schema_version':'1.0', 'study_id':protocol['study_id'], 'title':protocol['title'],
        'scope':'Synthetic dependency sensitivity grid; not a representative payment distribution or latency benchmark.',
        'seed':protocol['seed'], 'base_corpus_fingerprint':fingerprint(base),
        'profiles':copy.deepcopy(profiles), 'cases':resolved,
        'arm_order_by_case':{case['case_id']:order for case,order in zip(resolved,orders)},
    }


def mixed_case(index, phase, corpus):
    """Resolve one immutable scheduled input, independent of workflow arm."""
    if type(index) is not int or index < 0 or phase not in ('warmup','measurement'):
        raise ValueError('invalid_mixed_arrival')
    case = copy.deepcopy(corpus['cases'][index % len(corpus['cases'])])
    case['template_case_id'] = case['case_id']
    case['case_id'] = f'mixed-{phase}-{index}'
    case['payment']['payment_id'] = f'synthetic-mixed-{phase}-{index}'
    return case


def resolve_mixed_load(protocol, sensitivity, base):
    """Freeze a bounded mixed qualification; deadline misses remain reportable."""
    if not isinstance(protocol,dict) or protocol.get('schema_version')!='1.0' or protocol.get('study_id')!='w02_m1':
        raise ValueError('invalid_mixed_protocol')
    fixed={'worker_slots':2,'fixed_vus':16,'completion_poll_ms':100,'deadline_s':2,
           'drain_s':15,'deadline_miss_action':'report','case_selection':'repeat_seeded_grid_each_phase'}
    if any(protocol.get(k)!=v or type(protocol.get(k)) is not type(v) for k,v in fixed.items()):
        raise ValueError('mixed_controls_changed')
    warmup,duration=protocol.get('warmup_s'),protocol.get('duration_s')
    if any(type(v) is not int for v in (warmup,duration)) or warmup<2 or duration<20 or warmup%2 or duration%2:
        raise ValueError('invalid_mixed_phase_length')
    corpus=resolve_sensitivity(sensitivity,base)
    lanes=protocol.get('lanes')
    if not isinstance(lanes,list) or len(lanes)!=4:
        raise ValueError('mixed_lane_inventory_changed')
    wanted={('sequential',.5),('parallel',.5),('sequential',1),('parallel',1)}
    for lane in lanes:
        if not isinstance(lane,dict) or type(lane.get('rate')) not in (float,int) or lane.get('arm') not in ('sequential','parallel'):
            raise ValueError('invalid_mixed_lane')
        if (lane['arm'],lane['rate']) not in wanted or duration*lane['rate'] % len(corpus['cases']):
            raise ValueError('measurement_must_cover_complete_grid_cycles')
    if {(l['arm'],l['rate']) for l in lanes}!=wanted:
        raise ValueError('duplicate_or_missing_mixed_lane')
    return {'protocol':copy.deepcopy(protocol),'corpus':corpus,'lanes':copy.deepcopy(lanes),
            'warmup_s':warmup,'duration_s':duration}
