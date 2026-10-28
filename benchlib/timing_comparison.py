"""Fixed worker-timing design and exact ownership checks for its isolated database."""
import copy
import re

from .managed_recovery import engine_failover_spec


PAIR_ORDER = (('on', 'off'), ('off', 'on'), ('on', 'off'), ('off', 'on'), ('on', 'off'))


def measurement_plan():
    return {
        'pairs': [list(pair) for pair in PAIR_ORDER],
        'rate_per_s': 10, 'warmup_s': 600, 'measurement_s': 1800,
        'drain_s': 30, 'deadline_s': 2, 'poll_interval_ms': 100,
        'planned_correct_per_run': 18000,
        'stop_on_first_nonpassing_or_invalid_run': True,
        'only_candidate_control': 'runtime.worker_timing_enabled',
    }


def database_spec(source, run_id):
    """Copy allocation/network settings only, with no original state or endpoint."""
    result = engine_failover_spec(source, run_id)
    result.update(display_name='fluxnova-timing-' + run_id[:12],
                  description='Temporary isolated worker timing comparison; no original application cutover')
    result['freeform_tags'] = {'managed_by': 'fluxnova-benchmarks',
                               'comparison_run_id': run_id,
                               'temporary_comparison': 'true',
                               'comparison_lane': 'worker-timing'}
    return result


def require_owned_database(target, *, source_id, target_id, run_id):
    """Require a fresh GET and retained create ID before any target mutation."""
    tags = target.get('freeform_tags') or {}
    if (not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id)
            or not source_id or not target_id or source_id == target_id
            or target.get('id') != target_id
            or target.get('display_name') != 'fluxnova-timing-' + run_id[:12]
            or tags.get('managed_by') != 'fluxnova-benchmarks'
            or tags.get('comparison_run_id') != run_id
            or tags.get('temporary_comparison') != 'true'
            or tags.get('comparison_lane') != 'worker-timing'
            or (target.get('source') or {}).get('source_type') != 'NONE'
            or target.get('instance_count') != 2):
        raise ValueError('Database differs from the retained isolated comparison identity')


def require_matched_controls(before, after):
    """Reject an A/B pair when any recorded control other than timing changes."""
    values = []
    for item in (before, after):
        item = copy.deepcopy(item)
        enabled = item.get('runtime', {}).pop('worker_timing_enabled', None)
        if type(enabled) is not bool:
            raise ValueError('Each worker timing setting must be an explicit Boolean')
        values.append(item)
    if before['runtime']['worker_timing_enabled'] == after['runtime']['worker_timing_enabled']:
        raise ValueError('A matched pair must contain both worker timing settings')
    if values[0] != values[1]:
        raise ValueError('Recorded controls differ beyond worker timing')
