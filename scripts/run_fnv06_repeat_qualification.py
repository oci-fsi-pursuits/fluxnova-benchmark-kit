#!/usr/bin/env python3
"""Qualify six fresh local warmup/burst cells; never starts the performance study."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from benchlib.fnv06_functional import save,sha
from benchlib.fnv06_repeat import cell_protocol
from scripts.run_fnv06_load_qualification import run as run_cell, SOURCES
from scripts.audit_fnv06_load_qualification import audit
from scripts.ci_tools import ASSETS


def run(args):
    output=args.output.resolve()
    if output.exists():raise ValueError('Use a fresh directory; every earlier attempt must remain retained')
    corpus=json.loads((ROOT/'workloads/fnv06/repeat-qualification.json').read_text())
    for spec in corpus['cells']:cell_protocol(corpus,spec['id'])
    files=set(SOURCES)|{'benchlib/fnv06_pagination.py','benchlib/fnv06_repeat.py',
        'workloads/fnv06/scale-qualification.json','workloads/fnv06/repeat-qualification.json',
        'scripts/run_fnv06_repeat_qualification.py'}
    frozen={n:sha(ROOT/n) for n in sorted(files)}
    output.mkdir(parents=True);(output/'cells').mkdir();(output/'audits').mkdir()
    save(output/'plan.json',{'study_id':corpus['study_id'],'cells':corpus['cells'],'source_sha256':frozen,
        'created_at':datetime.now(timezone.utc).isoformat(),'fresh_local_h2_per_cell':True,
        'postgresql_reset_qualified':False,'resource_telemetry_qualified':False,'performance_comparison_complete':False})
    results=[];deadline=time.monotonic()+3600;seen=set();failure=None
    try:
        for spec in corpus['cells']:
            assert all(sha(ROOT/n)==v for n,v in frozen.items()),'Frozen source changed during collection'
            assert time.monotonic()+600<deadline,'Preserve cleanup time; do not start another cell'
            directory=output/'cells'/spec['id']
            run_cell(SimpleNamespace(output=directory,runtime=args.runtime,java=args.java,k6=args.k6,
                k6_archive=args.k6_archive,port=args.port,scale=False,repeat_cell=spec['id'],deadline_monotonic=deadline))
            result=audit(directory);assert result['qualification_cell']==spec and result['warmup']['status']=='passed'
            ids=set(json.loads((directory/'summary.json').read_text())['owned_process_ids'])
            assert not seen&ids;seen.update(ids)
            save(output/'audits'/(spec['id']+'.json'),result);results.append(result)
            save(output/'progress.json',{'cells_passed':[r['qualification_cell']['id'] for r in results],
                'last_workflow_instances':result['workflow_instances'],'cloud_resources_accessed':False})
            print(json.dumps({'cell':spec['id'],'status':'independently_audited','workflow_instances':result['workflow_instances']}),flush=True)
        assert all(results[i]['warmup']['signature']==results[i+1]['warmup']['signature'] for i in (0,2,4))
        assert len({json.loads((output/'cells'/s['id']/'manifest.json').read_text())['run_id'] for s in corpus['cells']})==6
        assert sum(r['workflow_instances'] for r in results)==3654 and len(seen)==3654
    except BaseException as error:
        failure=error
        save(output/'failure.json',{'error_type':type(error).__name__,'reason':str(error)[:250]})
        raise
    finally:
        summary={'study_id':corpus['study_id'],'status':'failed' if failure else 'passed',
            'cells_passed':[r['qualification_cell']['id'] for r in results],
            'workflow_instances':sum(r['workflow_instances'] for r in results),
            'warmup_completions':100*len(results),'measurement_phase_completions':sum(r['qualification_cell']['population'] for r in results),
            'unchanged_waiting_controls':sum(r['unchanged_waiting_controls'] for r in results),
            'warmup_signatures':{r['qualification_cell']['id']:r['warmup']['signature_sha256'] for r in results},
            'owned_engines_stopped':all(r['owned_engine_stopped'] for r in results) and not failure,
            'cloud_resources_accessed':False,'postgresql_reset_qualified':False,'resource_telemetry_qualified':False,
            'performance_comparison_complete':False,'scope':'Six fresh local H2 engines, fixed warmup state and native finite bursts; no OCI reset, headroom or performance claim.'}
        save(output/'summary.json',summary)
        save(output/'checksums.json',{'algorithm':'sha256','files':{p.relative_to(output).as_posix():sha(p)
             for p in sorted(output.rglob('*')) if p.is_file() and p!=output/'checksums.json'}})
    print(json.dumps(summary),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,default=ROOT/'.tools/fluxnova-3.0.1')
    parser.add_argument('--java',type=Path,default=ROOT/'.tools/java21/jdk-21.0.12.1+1-jre/bin/java.exe')
    parser.add_argument('--port',type=int,default=29381)
    asset=ASSETS[platform.system()]
    parser.add_argument('--k6',type=Path,default=ROOT/'.tools/ci'/asset[2])
    parser.add_argument('--k6-archive',type=Path,default=ROOT/'.tools/ci'/asset[0])
    run(parser.parse_args())


if __name__=='__main__':main()
