"""Frozen FNV-06 population matrix and append-only attempt accounting.

An explicit execution contract activates the unchanged historical design.
Qualification evidence is never relabeled as a campaign measurement.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DESIGN = 'workloads/fnv06/population-scaling-design.json'
EXECUTION = 'workloads/fnv06/population-scaling-execution.json'
STUDY = 'FNV-06-SCALING-1'
DESIGN_SHA = '4531874e518c9a6d3984bff5bc7fb74953202636599f5609c87be1482bbb70e4'
OUTCOME_SHA = 'aa26567508ae73dd4cd73da8b2b7d05d9c83ba1e2c09d5476d73fdb716239e74'
RESOURCE_SHA = '93d83ed9c0edd10b2c3996a1c57d866eb03ac64d893e31f197d6b4d2191da7ad'


def digest(raw): return hashlib.sha256(raw).hexdigest()
def canonical(path): return Path(path).read_bytes().replace(b'\r\n', b'\n')
def encoded(value): return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
def now(): return datetime.now(timezone.utc).isoformat()


def protocol(root=ROOT):
    root = Path(root)
    design_raw, execution_raw = canonical(root/DESIGN), canonical(root/EXECUTION)
    if digest(design_raw) != DESIGN_SHA:
        raise ValueError('Frozen population design changed')
    design, execution = json.loads(design_raw), json.loads(execution_raw)
    expected = {
        'schema_version': 1, 'study_id': STUDY, 'execution_phase': 'population_campaign_v1',
        'execution_enabled': True, 'design_sha256': DESIGN_SHA,
        'outcome_protocol_sha256': OUTCOME_SHA, 'resource_protocol_sha256': RESOURCE_SHA,
        'order': 'repeat, declared pattern order, declared population order',
        'cell_id_format': 'C001 through C045; fixed width preserves variable lengths',
        'request_failures_remain_failed': True, 'unknown_outcomes_stop_campaign': True,
        'missing_resource_evidence_stops_campaign': True, 'generator_constraints_remain_reported': True,
        'no_replacement_or_retry': True, 'all_planned_cells_in_report': True,
        'historical_design_file_unchanged': True,
        'required_qualification_evidence': ['validation/oci-20261101-fnv06-outcomes/summary.json',
                                             'validation/oci-20261101-fnv06-telemetry/summary.json']}
    if execution != expected:
        raise ValueError('Campaign execution contract changed')
    for name, sha in [('outcome-accounting.json', OUTCOME_SHA), ('resource-observation.json', RESOURCE_SHA)]:
        if digest(canonical(root/'workloads/fnv06'/name)) != sha:
            raise ValueError('Qualified observation protocol changed')
    cells = []
    for repeat, (patterns, populations) in enumerate(zip(design['pattern_order_by_repeat'], design['population_order_by_repeat'], strict=True), 1):
        for kind in patterns:
            for population in populations:
                cells.append({'id': 'C'+str(len(cells)+1).zfill(3), 'kind': kind,
                              'population': population, 'repeat': repeat})
    return {'study_id': STUDY, 'design_sha256': DESIGN_SHA, 'execution_sha256': digest(execution_raw),
            'population_sizes': [10,100,1000], 'warmup_recipients': 100,
            'message_client_ceiling': 4, 'controls_per_observation': 2, 'release_lead_ms': 3000,
            'maximum_first_dispatch_lag_ms': 1000, 'completion_poll_ms': 100,
            'completion_timeout_s': 120, 'batch_terminal_timeout_s': 240, 'cells': cells,
            'denominators': design['denominators']}


def cell_protocol(value, cell_id):
    expected = protocol()
    # Selected protocols can be revalidated without trusting their derived observations.
    base = {k:v for k,v in value.items() if k not in ('qualification_cell','observations')}
    if base != expected:
        raise ValueError('Campaign matrix or controls differ')
    cell = next((row for row in expected['cells'] if row['id'] == cell_id), None)
    if cell is None: raise ValueError('Unknown campaign cell')
    result = copy.deepcopy(expected)
    result['qualification_cell'] = cell
    result['observations'] = [{'id':cell_id+phase, 'kind':cell['kind'], 'population':n}
                              for phase,n in [('W',100),('M',cell['population'])]]
    return result


def require_binding(meta, *, root=ROOT):
    value = protocol(root)
    if (meta.get('qualification_protocol') != 'campaign'
            or meta.get('campaign_phase') != 'population_campaign_v1'
            or meta.get('campaign_design_sha256') != value['design_sha256']
            or meta.get('campaign_execution_sha256') != value['execution_sha256']
            or meta.get('qualification_cell') not in value['cells']
            or meta.get('qualification_phase') != 'outcome_accounting_v1'
            or meta.get('outcome_protocol_sha256') != OUTCOME_SHA
            or meta.get('resource_phase') != 'workload_resources_v1'
            or meta.get('resource_protocol_sha256') != RESOURCE_SHA
            or 'controlled_fault' in meta):
        raise ValueError('Campaign identity or qualified controls differ')
    return value


def create_ledger(folder, *, run_id, source_commit):
    if not re.fullmatch('[0-9a-f]{32}',run_id) or not re.fullmatch('[0-9a-f]{40}',source_commit):
        raise ValueError('Campaign identity is invalid')
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=False)
    (folder/'events').mkdir()
    plan = {'schema_version':1,'created_at':now(),'run_id':run_id,'source_commit':source_commit,
            'protocol':protocol()}
    with (folder/'plan.json').open('xb') as out: out.write(encoded(plan))
    return ledger(folder)


def _apply(state, event):
    rows = state['cells']; data = event['data']; kind = event['kind']
    if state['closed']: raise ValueError('Closed campaign cannot be changed')
    if kind == 'started':
        eligible = next((r for r in rows if r['status']=='unattempted'),None)
        if (state['stopped'] or eligible is None or data.get('cell') != eligible['cell']
                or any(r['status']=='running' for r in rows)):
            raise ValueError('Cell retry, replacement, overlap or reordered attempt')
        prior = rows[rows.index(eligible)-1] if rows.index(eligible) else None
        if prior and (prior['status']!='audited' or not re.fullmatch('[0-9a-f]{64}',data.get('reset_receipt_sha256',''))):
            raise ValueError('Prior audited cell and reset receipt required')
        if not prior and data.get('reset_receipt_sha256') is not None:
            raise ValueError('First cell cannot have a reset receipt')
        eligible.update(status='running', started_at=event['at'])
    elif kind in ('audited','failed'):
        current = next((r for r in rows if r['status']=='running'),None)
        if current is None or current['cell'] != data.get('cell'):
            raise ValueError('Result has no matching active attempt')
        if kind == 'audited':
            for key in ('seal_sha256','outcome_audit_sha256','resource_audit_sha256'):
                if not re.fullmatch('[0-9a-f]{64}', data.get(key,'')): raise ValueError('Missing audit binding')
            if (type(data.get('failed_requests')) is not int or data['failed_requests']<0
                    or type(data.get('generator_headroom_passed')) is not bool
                    or data.get('outcomes_verified') is not True or data.get('resources_verified') is not True):
                raise ValueError('Incomplete cell cannot be promoted to audited')
        else:
            if not isinstance(data.get('reason'),str) or not data['reason']:
                raise ValueError('Failed attempt needs a retained reason')
            state['stopped'] = True
        current.update(status=kind, finished_at=event['at'], result=copy.deepcopy(data))
    elif kind == 'closed':
        if any(r['status']=='running' for r in rows): raise ValueError('Active attempt must be accounted for')
        if not isinstance(data.get('reason'),str) or not data['reason']: raise ValueError('Close reason required')
        state.update(closed=True, stopped=True, close_reason=data['reason'])
    else: raise ValueError('Unknown campaign event')


def ledger(folder):
    folder = Path(folder); raw = (folder/'plan.json').read_bytes(); plan = json.loads(raw)
    if plan['protocol'] != protocol(): raise ValueError('Ledger plan differs from frozen campaign')
    state = {'plan':plan, 'plan_sha256':digest(raw), 'closed':False, 'stopped':False,
             'cells':[{'cell':copy.deepcopy(c), 'status':'unattempted'} for c in plan['protocol']['cells']]}
    previous = digest(raw); count = 0; previous_at = datetime.fromisoformat(plan['created_at'])
    files = sorted((folder/'events').iterdir())
    for index,path in enumerate(files,1):
        if path.name != str(index).zfill(4)+'.json' or path.is_symlink(): raise ValueError('Campaign event sequence changed')
        raw_event = path.read_bytes(); event = json.loads(raw_event)
        at = datetime.fromisoformat(event['at'])
        if event['sequence'] != index or event['previous_sha256'] != previous or at.tzinfo is None or at < previous_at:
            raise ValueError('Campaign event chain changed')
        _apply(state,event); previous = digest(raw_event); previous_at = at; count = index
    state.update(event_count=count, head_sha256=previous)
    state['counts'] = {key:sum(r['status']==key for r in state['cells']) for key in ('unattempted','running','audited','failed')}
    return state


def append_event(folder, kind, data):
    folder = Path(folder); state = ledger(folder)
    event = {'sequence':state['event_count']+1, 'previous_sha256':state['head_sha256'],
             'at':now(), 'kind':kind, 'data':copy.deepcopy(data)}
    _apply(state,event)  # Validate before exclusive creation; never overwrite a result.
    path = folder/'events'/(str(event['sequence']).zfill(4)+'.json')
    with path.open('xb') as out: out.write(encoded(event))
    return ledger(folder)
