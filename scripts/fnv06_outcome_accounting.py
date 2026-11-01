"""Separate request success, verified final outcomes and deadline uncertainty."""
import hashlib,json,math
from pathlib import Path

PROTOCOL=Path(__file__).resolve().parents[1]/'workloads/fnv06/outcome-accounting.json'

def protocol():
    raw=PROTOCOL.read_bytes().replace(b'\r\n',b'\n');value=json.loads(raw)
    assert value['schema_version']==1 and value['study_id']=='FNV-06-OUTCOME-ACCOUNTING'
    assert value['underlying_study']=='FNV-06-REPEAT-QUAL' and value['qualification_phase']=='outcome_accounting_v1'
    assert (value['synthetic_deadline_s'],value['http_timeout_s'],value['recipient_drain_s'],value['batch_terminal_s'])==(2,30,120,240)
    assert value['warmup_requires_successful_requests'] is True and value['failed_requests_remain_failed'] is True
    return value,hashlib.sha256(raw).hexdigest()

def request_result(row,expected_status):
    """Called only with native actions whose identity, input and timing are audited."""
    status,code=row['status'],row['error_code']
    assert type(status) is int and (status==0 or 100<=status<=599)
    assert type(code) is int and code>=0
    assert math.isfinite(row['elapsed_ms']) and 0<=row['elapsed_ms']<=30100
    if status==expected_status:
        assert code==0
        return 'successful'
    if status==0:
        assert code>0 and row['response'] is None
        if code==1050:
            assert 29900<=row['elapsed_ms']<=30100
            return 'timeout'
        return 'transport_failure'
    return 'http_failure'

def deadline_bounds(polls,population,*,deadline_s=2):
    """Bound counts only after original poll requests and final correctness pass."""
    assert type(population) is int and population>0 and deadline_s==2 and polls
    previous=-1;previous_finish=-float('inf')
    for row in polls:
        start,finish=row['start_elapsed_s'],row['finish_elapsed_s'];count=row['count']
        assert all(type(v) in (int,float) and math.isfinite(v) for v in (start,finish))
        assert previous_finish<=start<=finish and type(count) is int and previous<=count<=population
        previous=count;previous_finish=finish
    assert polls[0]['count']==0 and polls[-1]['count']==population
    lower=max((p['count'] for p in polls if p['finish_elapsed_s']<=deadline_s),default=0)
    upper=min((p['count'] for p in polls if p['start_elapsed_s']>=deadline_s),default=population)
    assert 0<=lower<=upper<=population
    end=polls[-1]['finish_elapsed_s'];begin=max(0,polls[-2]['start_elapsed_s']) if len(polls)>1 else 0
    status='confirmed_on_time' if end<=deadline_s else 'confirmed_late' if begin>=deadline_s else 'uncertain'
    return {'synthetic_deadline_s':deadline_s,'correct_by_deadline_lower_bound':lower,
        'correct_by_deadline_upper_bound':upper,'deadline_uncertain':upper-lower,
        'correct_after_deadline_lower_bound':population-upper,'cohort_deadline_status':status,
        'count_only_not_individual_completion_timestamps':True}

def declared_for_reset(folder,identity,source):
    value,digest=protocol()
    if identity.get('qualification_phase')!=value['qualification_phase']:return False
    path=Path(folder)/'inputs/workloads/fnv06/outcome-accounting.json'
    assert path.read_bytes()==PROTOCOL.read_bytes().replace(b'\r\n',b'\n')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==source['files']['workloads/fnv06/outcome-accounting.json']==identity['outcome_protocol_sha256']==digest
    if identity.get('qualification_protocol')=='campaign':
        from benchlib.fnv06_campaign import require_binding,DESIGN,EXECUTION
        require_binding(identity,root=Path(folder)/'inputs')
        for name,key in [(DESIGN,'campaign_design_sha256'),(EXECUTION,'campaign_execution_sha256')]:
            assert hashlib.sha256((Path(folder)/'inputs'/name).read_bytes()).hexdigest()==source['files'][name]==identity[key]
    return True
