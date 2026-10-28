#!/usr/bin/env python3
"""Build an offline W02 walkthrough from checksum-verified functional evidence."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchlib.workflow_checks_report import diagram
from scripts.run_w02_functional import require_outcome


PAGE='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fluxnova | Correct payments, different workflows</title><style>
:root{--ink:#153e3a;--blue:#0677e8;--mint:#00b993;--paper:#f3f6f5;--line:#d4e1de;--muted:#526a65}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,sans-serif}main{max-width:1220px;margin:auto;padding:32px 32px 24px}header{display:flex;justify-content:space-between;gap:24px;align-items:start}.brand{font-weight:850;font-size:23px;color:var(--blue);letter-spacing:1px}.eyebrow{font-size:12px;font-weight:750;letter-spacing:1.2px;text-transform:uppercase}.badge{font-size:13px;border:1px solid #b8cfca;border-radius:40px;padding:7px 12px;white-space:nowrap}.badge:before{content:'●';color:var(--mint);margin-right:7px}h1{font-size:38px;line-height:1.15;letter-spacing:-1px;max-width:900px;margin:22px 0 12px}h2{font-size:20px;line-height:1.25;margin:0 0 12px}h3{font-size:16px;margin:0 0 10px}.lead{color:var(--muted);max-width:950px;margin:0 0 24px}.scope{background:#e5efec;border-left:4px solid var(--mint);padding:12px 16px;font-size:14px}.toolbar{display:flex;gap:18px;align-items:end;flex-wrap:wrap;margin:24px 0 18px}.field{flex:1;min-width:240px}label{font-size:13px;font-weight:700;display:block;margin-bottom:5px}select,button{font:inherit;border:1px solid #abc3bd;border-radius:7px;padding:9px 14px;background:white;color:var(--ink)}select{width:100%;min-height:46px}button{cursor:pointer}button[aria-pressed=true]{background:var(--ink);color:white;border-color:var(--ink)}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--blue);outline-offset:3px}.switch{display:flex;gap:7px}.panel{background:white;border:1px solid var(--line);border-radius:12px;padding:22px}.diagram{margin-bottom:18px}.diagram svg{width:100%;max-height:390px;font-family:system-ui,sans-serif}.diagram-head{display:flex;justify-content:space-between;gap:20px;align-items:baseline}.diagram-head p{font-size:13px;color:var(--muted);margin:0}.grid{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:16px}.num{font-size:28px;font-weight:800;line-height:1.2}.small{font-size:13px;color:var(--muted)}dl{margin:14px 0 0}dl div{display:flex;justify-content:space-between;gap:14px;padding:6px 0;border-bottom:1px solid #edf2f0}dt{color:var(--muted);font-size:13px}dd{margin:0;font-size:13px;text-align:right;overflow-wrap:anywhere}.pill{display:inline-block;background:#e3f3ed;color:#0a6047;border-radius:5px;padding:3px 8px;font-size:13px;font-weight:700}.timeline{margin-top:14px}.track{height:12px;background:#edf2f1;border-radius:7px;margin:5px 0 14px;position:relative}.bar{height:12px;min-width:2px;border-radius:7px;position:absolute;background:var(--blue)}.bar.risk{background:var(--mint)}.bar.settle{background:#345e56}.track-label{font-size:12px;display:flex;justify-content:space-between;gap:12px}.outcome{margin:14px 0;font-size:16px;font-weight:750}.checks{font-size:13px;margin:12px 0 0;padding-left:18px}.checks li{margin:6px 0}.note{margin-top:18px;font-size:14px;color:var(--muted)}.totals{display:flex;gap:24px;flex-wrap:wrap;margin:24px 0 14px;padding-top:18px;border-top:1px solid var(--line);font-size:14px}.totals b{color:var(--ink)}footer{font-size:12px;color:var(--muted);margin-top:20px;padding-top:16px;border-top:1px solid var(--line)}a{color:#0065bc}footer a{margin-right:16px}[hidden]{display:none!important}@media(max-width:900px){.grid{grid-template-columns:1fr}.diagram svg{max-height:none}h1{font-size:31px}main{padding:22px 18px}header{display:block}.badge{display:inline-block;margin-top:12px}.diagram-head{display:block}}@media print{.toolbar{display:none}body{background:white}main{padding:0}.grid{grid-template-columns:1fr 1.2fr 1fr}.badge{white-space:normal}}
</style></head><body><main>
<header><div><div class="brand">FLUXNOVA</div><div class="eyebrow">Research walkthrough · W02</div></div><div class="badge">Saved functional evidence · works offline</div></header>
<h1>Correct payments.<br>Different ways to run the checks.</h1>
<p class="lead">Keep the original payment, policy checks and two worker slots fixed. Change whether KYC and risk run sequentially or in parallel. Both checks must finish before a synthetic payment can be authorized.</p>
<div class="scope"><b>This is a saved local engine run.</b> The dependencies are deterministic. No live payment or model is called. These functional cases do not establish a performance improvement; the separate matched OCI experiment measures that question.</div>
<div class="toolbar"><div class="field"><label for="case">Choose a recorded case</label><select id="case"></select></div><div><label>Workflow</label><div class="switch" role="group" aria-label="Choose workflow"><button id="sequential" type="button" aria-pressed="true">Sequential checks</button><button id="parallel" type="button" aria-pressed="false">Parallel checks</button></div></div></div>
<section class="panel diagram"><div class="diagram-head"><h2 id="workflow-title">Sequential checks</h2><p>Actual BPMN used by the saved run</p></div><div id="sequential-diagram">__SEQUENTIAL__</div><div id="parallel-diagram" hidden>__PARALLEL__</div></section>
<div class="grid"><section class="panel"><h3>1 · Original payment</h3><div class="num" id="amount"></div><div class="small">minor units · synthetic payment</div><dl id="payment"></dl></section>
<section class="panel"><h3>2 · Independent checks</h3><div class="pill" id="overlap"></div><div class="timeline" id="timeline"></div><p class="small" id="axis"></p><p class="small">Receipt timings for this one functional execution, relative to its first check. They are not p95 latency or a throughput comparison.</p></section>
<section class="panel"><h3>3 · Verified outcome</h3><div class="num" id="effect-count"></div><div class="small">synthetic payment effects</div><p class="outcome" id="outcome"></p><ul class="checks" id="safety"></ul><p class="small"><a id="record" href="#">Inspect this saved record</a></p></section></div>
<p class="note" id="case-note"></p>
<div class="totals"><span><b>20</b> functional cases passed</span><span><b>8</b> expected effects</span><span><b>120</b> safe repeated calls</span><span><b>2</b> cross-process replays blocked</span></div>
<footer>Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.<br>Evidence: local functional run · two fixed worker slots · owned engine stopped after verification.<br><a href="evidence/summary.json">Source summary</a><a href="evidence/manifest.json">Original runtime and inputs</a><a href="bundle-manifest.json">Bundle provenance</a><a href="https://github.com/oci-fsi-pursuits/fluxnova-benchmarking">Source and reproduction guide</a></footer>
</main><script id="evidence-data" type="application/json">__DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('evidence-data').textContent);
const select=document.getElementById('case');let arm='sequential';
const labels={approve:'Approved payment',sanctions_reject:'Sanctions match',risk_reject:'Payment above the limit',unauthorized:'Unauthorized payment',review_required:'Review required: settlement refused',unknown_kyc:'Unknown KYC result',unknown_risk:'Unknown risk result',retry_before_receipt:'Failure before a receipt',reconcile_after_receipt:'Lost response after a receipt',concurrent_completion:'Concurrent completion'};
for(const c of data){const option=document.createElement('option');option.value=c.case_id;option.textContent=labels[c.case_id]||c.case_id.replaceAll('_',' ');select.append(option)}
function text(id,value){document.getElementById(id).textContent=value}
function row(parent,label,value){const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;div.append(dt,dd);parent.append(div)}
function render(){
 const c=data.find(x=>x.case_id===select.value),r=c.arms[arm],p=c.payment;
 for(const name of ['sequential','parallel']){document.getElementById(name).setAttribute('aria-pressed',String(name===arm));document.getElementById(name+'-diagram').hidden=name!==arm}
 text('workflow-title',arm==='sequential'?'Sequential checks':'Parallel checks');text('amount',p.amount_minor.toLocaleString());
 const payment=document.getElementById('payment');payment.replaceChildren();
 for(const [label,value] of [['Authorized',p.authorized?'Yes':'No'],['Limit',p.limit_minor+' minor units'],['Sanctions match',p.sanctions_match?'Yes':'No'],['High risk',p.high_risk?'Yes':'No'],['Market data',p.stale_market?'Stale':'Current']])row(payment,label,value);
 text('overlap',r.overlap?'KYC and risk overlapped':'KYC and risk did not overlap');
 const timeline=document.getElementById('timeline');timeline.replaceChildren();
 const span=Math.max(...Object.values(c.arms).flatMap(x=>x.receipts.map(y=>y.end_ms)),1);
 text('axis','Same 0–'+span.toFixed(1)+' ms axis for both workflows in this case.');
 for(const receipt of r.receipts){const label=document.createElement('div');label.className='track-label';const name=document.createElement('span'),time=document.createElement('span');name.textContent=(receipt.operation==='settle'?'Verify / settle':receipt.operation.toUpperCase())+' · '+receipt.outcome;time.textContent=receipt.start_ms.toFixed(1)+'–'+receipt.end_ms.toFixed(1)+' ms';label.append(name,time);const track=document.createElement('div'),bar=document.createElement('div');track.className='track';bar.className='bar '+receipt.operation;bar.style.left=(receipt.start_ms/span*100)+'%';bar.style.width=((receipt.end_ms-receipt.start_ms)/span*100)+'%';track.append(bar);timeline.append(label,track)}
 text('effect-count',r.effects);text('outcome',r.outcome==='settled'?'Original payment settled exactly once':'No payment: '+r.outcome.replaceAll('_',' '));
 const safety=document.getElementById('safety');safety.replaceChildren();
 for(const value of ['Original inputs and both receipts verified','Premature settlement refused',r.replay_calls+' repeated calls preserved the result']){const li=document.createElement('li');li.textContent=value;safety.append(li)}
 document.getElementById('record').href='evidence/cases/'+arm+'-'+c.case_id+'.json';
 const faults=Object.entries(c.schedule).filter(([_,s])=>s.fault!=='none').map(([op,s])=>op.toUpperCase()+': '+s.fault.replaceAll('_',' '));
 text('case-note','Configured dependency waits: KYC '+c.schedule.kyc.delay_ms+' ms; risk '+c.schedule.risk.delay_ms+' ms. '+(faults.length?'Injected condition: '+faults.join('; ')+'. ':'')+'The full comparison holds these inputs fixed and measures many scheduled arrivals.');
}
select.addEventListener('change',render);for(const name of ['sequential','parallel'])document.getElementById(name).addEventListener('click',()=>{arm=name;render()});render();
</script></body></html>'''


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def display_diagram(path):
    """Keep the original graph geometry; give labels space around connectors."""
    figure=ET.fromstring(diagram(path))
    figure.set('viewBox','-15 15 840 355')
    figure.set('style','font-family:Arial,sans-serif;fill:#153e3a')
    for label in figure.findall('text'):
        if label.text in {'Run independent','checks','Wait for both','Outcome'}:
            label.set('x',str(float(label.get('x'))+10))
            label.set('y',str(float(label.get('y'))+5))
            label.set('text-anchor','start')
    return ET.tostring(figure,encoding='unicode')


def build(source,output):
    source,output=Path(source).resolve(),Path(output).resolve()
    archive=output.with_suffix('.zip')
    if output.exists():raise ValueError('Use a new output directory; retain earlier artifacts.')
    if archive.exists():raise ValueError('Retain existing archive and choose a fresh output name.')
    if output.is_relative_to(source):raise ValueError('Keep the bundle outside the original evidence directory.')
    checksum=read(source/'checksums.json')
    for relative,wanted in checksum['files'].items():
        path=(source/relative).resolve()
        if not path.is_relative_to(source) or sha(path)!=wanted:raise ValueError('Source evidence checksum mismatch: '+relative)
    def sealed(relative):
        if relative not in checksum['files']:raise ValueError('Required source is not sealed: '+relative)
        return source/relative
    summary=read(sealed('summary.json'))
    if summary['scope']!='LOCAL FUNCTIONAL VALIDATION - NOT OCI PERFORMANCE' or summary['status']!='passed' or summary['functional_cases_passed']!=20 or not summary['owned_engine_stopped']:
        raise ValueError('Expected the completed 20-case local functional source, not partial or performance evidence.')
    runtime=read(sealed('manifest.json'))
    if runtime['worker_slots']!=2 or runtime['native_model_calls']!=0 or runtime['cloud_resources_accessed']:
        raise ValueError('The saved walkthrough requires two worker slots and no cloud or model calls.')
    expected={case['case_id']:case for case in read(sealed('inputs/workloads/w02/cases.json'))['cases']}
    grouped=defaultdict(dict);selected={};effects=repeats=0
    for row in summary['cases']:
        name=row['arm']+'-'+row['case_id']+'.json';record=read(sealed(name));case=record['case'];backend=record['backend_before_replay']
        if case!=expected[row['case_id']] or row!=record['result'] or row['arm'] not in ('sequential','parallel') or row['status']!='passed':raise ValueError('Unexpected case or outcome metadata')
        require_outcome(case,backend,record['history'],record['activities'])
        after=record['backend_after_replay']
        if after['effects']!=backend['effects'] or after['receipts']!=backend['receipts'] or not row['premature_effect_refused'] or row['replay_calls']!=6:raise ValueError('Outcome controls not preserved')
        receipts=backend['receipts'];origin=min(r['started_ns'] for r in receipts.values())
        timing=[{'operation':op,'outcome':receipts[op]['outcome'],'start_ms':(receipts[op]['started_ns']-origin)/1e6,
                 'end_ms':(receipts[op]['finished_ns']-origin)/1e6} for op in ('kyc','risk','settle')]
        overlap=min(receipts['kyc']['finished_ns'],receipts['risk']['finished_ns'])>max(receipts['kyc']['started_ns'],receipts['risk']['started_ns'])
        if overlap!=row['checks_overlap']:raise ValueError('Receipt overlap differs from recorded assertion')
        if row['arm'] in grouped[case['case_id']]:raise ValueError('Duplicate case arm')
        grouped[case['case_id']][row['arm']]={'outcome':row['outcome'],'effects':len(backend['effects']),'overlap':overlap,'replay_calls':row['replay_calls'],'receipts':timing}
        effects+=len(backend['effects']);repeats+=row['replay_calls'];selected['evidence/cases/'+name]=source/name
    if len(grouped)!=10 or any(set(arms)!={'sequential','parallel'} for arms in grouped.values()) or effects!=8 or repeats!=120:raise ValueError('Incomplete functional corpus')
    if len(summary['replay_probes'])!=2 or not all(p['status']=='blocked_as_expected' for p in summary['replay_probes']):raise ValueError('Replay probes not verified')
    data=[{'case_id':key,'payment':expected[key]['payment'],'schedule':expected[key]['schedule'],'arms':arms} for key,arms in grouped.items()]
    figures={arm:display_diagram(sealed('inputs/workloads/w02/'+arm+'.bpmn')) for arm in ('sequential','parallel')}
    content=PAGE.replace('__SEQUENTIAL__',figures['sequential']).replace('__PARALLEL__',figures['parallel']).replace('__DATA__',json.dumps(data,ensure_ascii=True).replace('<',r'\u003c'))
    output.mkdir(parents=True)
    (output/'index.html').write_text(content,encoding='utf-8',newline='\n')
    selected.update({'evidence/summary.json':source/'summary.json','evidence/manifest.json':source/'manifest.json',
                     'evidence/original-checksums.json':source/'checksums.json'})
    for arm in ('sequential','parallel'):
        selected['assets/'+arm+'.bpmn']=source/'inputs/workloads/w02'/(arm+'.bpmn')
        (output/'assets').mkdir(exist_ok=True)
        (output/'assets'/(arm+'.svg')).write_text(figures[arm].replace('<svg ','<svg xmlns="http://www.w3.org/2000/svg" ',1),encoding='utf-8')
    for relative,path in selected.items():
        target=output/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    (output/'README.md').write_text('''# W02 saved-evidence demo

Open `index.html` directly in a browser. No server, cloud sign-in, live engine or model is needed. This bundle contains selected synthetic functional evidence, not the separate OCI performance comparison.

Suggested 60–90 second walkthrough:

1. Show the approved payment with sequential checks. Explain the fixed payment, policy inputs and two worker slots.
2. Switch to parallel checks. Point out the join and the saved receipt overlap. Do not call this an end-to-end speedup measurement.
3. Choose an unauthorized or sanctions case. Show that a correct outcome can be a refusal with zero payment effects.
4. Explain that the separate frozen five-pair study measures latency and deadline goodput under continuous arrivals.

The bundle builder verifies the complete source checksum inventory and rechecks all twenty original outcomes. Selected records retain their original bytes. `bundle-manifest.json` seals this bundle; `evidence/original-checksums.json` describes the larger original run, including files not copied here. Generated credentials, engine configuration and databases are not included.

Interactive browser and projector review remain pending. Data verification alone is not visual approval.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
''',encoding='utf-8',newline='\n')
    manifest={'scope':summary['scope'],'source_run_id':summary['run_id'],'source_checksum_inventory_sha256':sha(source/'checksums.json'),
              'source_files_verified':len(checksum['files']),'selected_original_sha256':{name:sha(path) for name,path in selected.items()},
              'builder_sha256':sha(__file__),'functional_cases':20,'expected_effects':8,'repeat_calls':120,'model_calls':0,
              'interactive_browser_review':'pending; building a bundle does not establish interactive review',
              'files':{p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*')) if p.is_file()}}
    (output/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(output.rglob('*')):
            if path.is_file():zipped.write(path,path.relative_to(output).as_posix())
    return {'viewer':str(output/'index.html'),'archive':str(archive),'functional_cases_verified':20,
            'source_files_verified':len(checksum['files']),'interactive_review':'pending'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build(args.source,args.output),indent=2))
