"""Audit raw counter coverage; independent of the live cgroup reader.

The interval must be enclosed by counter reads on both sides. No throttling is
permitted for the selected generator or its ancestors. Engine throttling, when
reported separately, is a system outcome rather than a generator-validity gate.
These engineering limits do not prove peak CPU capacity or a production SLO.
"""
import math
from pathlib import PurePosixPath


def number(value):return type(value) in (int,float) and math.isfinite(value)

def keyed(raw):
    assert isinstance(raw,str)
    result={}
    for line in raw.splitlines():
        parts=line.split();assert len(parts)==2 and parts[0] not in result
        assert parts[1].isdigit();result[parts[0]]=int(parts[1])
    return result

def limit(raw):
    assert isinstance(raw,str)
    if raw.strip()=='max':return None
    assert raw.strip().isdigit() and int(raw)>0
    return int(raw)

def quota(raw):
    assert isinstance(raw,str);values=raw.split();assert len(values)==2
    maximum=limit(values[0]);period=limit(values[1]);assert period is not None
    return maximum,period

def counters(files,*,leaf):
    cpu=keyed(files['cpu.stat']) if files['cpu.stat'] is not None else {}
    maximum=quota(files['cpu.max']) if files['cpu.max'] is not None else None
    if leaf or maximum is not None and maximum[0] is not None:
        assert {'usage_usec','user_usec','system_usec','nr_periods','nr_throttled','throttled_usec'}<=set(cpu)
    memory_max=limit(files['memory.max']) if files['memory.max'] is not None else None
    current=int(files['memory.current']) if files['memory.current'] is not None else None
    peak=int(files['memory.peak']) if files['memory.peak'] is not None else None
    events=keyed(files['memory.events']) if files['memory.events'] is not None else {}
    if leaf or memory_max is not None:
        assert current is not None and current>=0 and {'high','max','oom','oom_kill'}<=set(events)
        if peak is not None:assert peak>=current
    if leaf:assert maximum is not None and maximum[0] is not None and memory_max is not None
    return {'cpu':cpu,'quota':maximum,'memory_max':memory_max,'memory_current':current,'memory_peak':peak,'events':events}

def host(raw):
    parts=raw['stat'].split();assert parts[0]=='cpu' and len(parts)>=9
    ticks=[int(v) for v in parts[1:9]];assert all(v>=0 for v in ticks)
    values={}
    for row in raw['meminfo'].splitlines():
        key,value=row.split(':',1)
        if key in ('MemTotal','MemAvailable'):
            fields=value.split();assert len(fields)==2 and fields[1]=='kB' and key not in values
            values[key]=int(fields[0])
    assert 0<=values['MemAvailable']<=values['MemTotal'] and values['MemTotal']>0
    return sum(ticks),ticks[3]+ticks[4],values['MemAvailable']/values['MemTotal']


def audit_window(rows,*,start_epoch_ms,finish_epoch_ms,generator_role='generator',role_kind='generator'):
    assert role_kind in ('generator','engine')
    assert number(start_epoch_ms) and number(finish_epoch_ms) and start_epoch_ms<finish_epoch_ms
    assert len(rows)>=3
    origin=rows[0]['started_epoch_ms']/1000-rows[0]['started_monotonic_s']
    previous=None
    for row in rows:
        assert not row.get('error')
        assert all(number(row[k]) for k in ('started_epoch_ms','finished_epoch_ms','started_monotonic_s','finished_monotonic_s'))
        assert row['started_monotonic_s']<=row['finished_monotonic_s']
        assert 0<=row['finished_monotonic_s']-row['started_monotonic_s']<=.1
        assert row['started_epoch_ms']<=row['finished_epoch_ms']
        for side in ('started','finished'):
            assert abs(row[side+'_epoch_ms']/1000-row[side+'_monotonic_s']-origin)<=.05,'Resource clock disagreement'
        if previous is not None:assert row['started_monotonic_s']>previous['finished_monotonic_s']
        previous=row
    before=[i for i,r in enumerate(rows) if r['finished_epoch_ms']<=start_epoch_ms]
    after=[i for i,r in enumerate(rows) if r['started_epoch_ms']>=finish_epoch_ms]
    assert before and after,'Resource counters do not enclose the declared work interval'
    selected=rows[max(before):min(after)+1];assert len(selected)>=2
    assert max(b['started_monotonic_s']-a['started_monotonic_s'] for a,b in zip(selected,selected[1:]))<=.25,'Resource sampling gap'
    binding=selected[0]['containers'][generator_role]['binding'];assert binding['pid']>0 and binding['process_start_ticks']>0 and binding['cgroup_inode']>0
    path=PurePosixPath(binding['cgroup']);assert path.is_absolute() and str(path)!='/' and '..' not in path.parts
    parents={str(p) for p in path.parents};assert '/' in parents
    decoded=[];hosts=[];issues=[]
    for row in selected:
        item=row['containers'][generator_role]
        assert item['binding']==binding and set(item['ancestors'])==parents,'Container identity or ancestors changed'
        decoded.append({'leaf':counters(item['files'],leaf=True),
                        **{name:counters(files,leaf=False) for name,files in item['ancestors'].items()}})
        hosts.append(host(row['host']))
    for name in decoded[0]:
        first,last=decoded[0][name],decoded[-1][name]
        assert all(r[name]['quota']==first['quota'] and r[name]['memory_max']==first['memory_max'] for r in decoded),'Resource limits changed'
        for left,right in zip(decoded,decoded[1:]):
            for group in ('cpu','events'):
                assert set(left[name][group])==set(right[name][group])
                assert all(right[name][group][k]>=v for k,v in left[name][group].items()),'Resource counters decreased'
        if any(last['cpu'].get(k,0)>first['cpu'].get(k,0) for k in ('nr_throttled','throttled_usec')):
            issues.append(role_kind+'_or_ancestor_cpu_throttled:'+name)
        if any(last['events'].get(k,0)>first['events'].get(k,0) for k in ('high','max','oom','oom_kill','oom_group_kill')):
            issues.append(role_kind+'_or_ancestor_memory_pressure:'+name)
        if first['memory_max'] is not None:
            peaks=[r[name]['memory_peak'] for r in decoded]
            if any(v is None for v in peaks):issues.append('lifetime_memory_peak_unavailable:'+name)
            elif max(peaks)>=.8*first['memory_max']:issues.append(role_kind+'_or_ancestor_memory_headroom_low:'+name)
    busy=[]
    for a,b in zip(hosts,hosts[1:]):
        total=b[0]-a[0];idle=b[1]-a[1];assert total>0 and 0<=idle<=total
        busy.append(1-idle/total)
    if max(busy)>=.8:issues.append('sampled_host_cpu_headroom_low')
    if min(v[2] for v in hosts)<.2:issues.append('sampled_host_memory_headroom_low')
    result = {'status':'passed' if not issues else 'resource_constrained','evidence_complete':True,
        'generator_headroom_passed':not issues,'issues':issues,'samples_covering_window':len(selected),
        'bracket_epoch_ms':[selected[0]['started_epoch_ms'],selected[-1]['finished_epoch_ms']],
        'generator_cpu_usage_delta_usec':decoded[-1]['leaf']['cpu']['usage_usec']-decoded[0]['leaf']['cpu']['usage_usec'],
        'generator_throttled_delta_usec':decoded[-1]['leaf']['cpu']['throttled_usec']-decoded[0]['leaf']['cpu']['throttled_usec'],
        'maximum_sampled_host_cpu_busy_fraction':max(busy),'minimum_sampled_host_available_memory_fraction':min(v[2] for v in hosts),
        'limits':{'max_sample_gap_s':.25,'max_read_duration_s':.1,'clock_tolerance_s':.05,'max_sampled_host_cpu_fraction':.8,
                  'min_host_available_memory_fraction':.2,'max_lifetime_container_memory_fraction':.8},
        'scope':'Raw counters bracketing finite work, including ancestor throttling; sampled host utilization and lifetime container memory peak. No peak CPU-capacity or database-CPU claim.'}
    if role_kind == 'engine':
        result.pop('generator_headroom_passed')
        result['engine_constraints_observed'] = bool(issues)
        for key in ('cpu_usage_delta_usec','throttled_delta_usec'):
            result['engine_'+key] = result.pop('generator_'+key)
        result['scope'] = 'Owned engine resource observations. Constraints are system outcomes and do not decide generator validity. No peak capacity claim.'
    return result
