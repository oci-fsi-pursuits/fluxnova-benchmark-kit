#!/usr/bin/env python3
"""Build an interactive W01 burst viewer only from a complete audited campaign."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.summarize_w01_cloud import recheck


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


HTML='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>W01 · Worker pacing during payment bursts</title>
<style>
:root{font-family:Arial,sans-serif;color:#173e38;background:#edf2ef}*{box-sizing:border-box}body{margin:0}main{max-width:1480px;margin:auto;padding:30px}h1{font-size:40px;margin:10px 0 14px}h2{font-size:22px;margin:0 0 14px}.eyebrow{font-size:14px;letter-spacing:1px;font-weight:bold;color:#466a61}p{line-height:1.5}.lead{font-size:20px;max-width:1100px}.scope{border-left:5px solid #d27c23;background:#fff5e6;padding:14px 18px;line-height:1.5}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}.stat,.panel{padding:20px;border:1px solid #cad7d1;background:white;border-radius:12px}.stat b{display:block;font-size:29px;margin-bottom:9px}.stat span{font-size:16px;line-height:1.4}.toolbar{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:25px 0}.toolbar label{font-weight:bold}select{font-size:17px;padding:10px 16px;border:1px solid #557f73;border-radius:7px;background:white;color:#173e38}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}svg{width:100%;height:auto}svg text{font-family:Arial,sans-serif;font-size:14px;fill:#173e38}.axis{stroke:#cad7d1;stroke-width:1}.mark{stroke-width:7;stroke-linecap:round}.on-time{stroke:#168158}.late{stroke:#bf5d1d}.deadline{stroke:#8b5a20;stroke-width:2}.legend{display:flex;gap:20px;font-size:15px;flex-wrap:wrap}.legend b{display:inline-block;width:20px;height:6px;vertical-align:middle;margin-right:7px}.note{font-size:15px;line-height:1.5}.metric{font-size:19px;font-weight:bold}.flow{padding:11px;background:#e7f2eb;border-radius:7px;font-size:16px;line-height:1.4}a{color:#075cac}.foot{font-size:14px;margin-top:25px;line-height:1.5}select:focus-visible,a:focus-visible{outline:3px solid #1375dd;outline-offset:3px}@media(max-width:950px){.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}main{padding:20px}h1{font-size:32px}}
</style><main>
<div class="eyebrow">FLUXNOVA BENCHMARKING · W01</div><h1>Worker pacing during payment bursts</h1>
<p class="lead">The same 20 payments arrive over one second, every ten seconds. Compare correct completions against the same two-second deadline.</p>
<div class="scope"><b>Saved matched OCI measurements.</b> The 100 ms success delay is a reconstructed historical control. Current default behavior has no success delay. Neither arm uses a live model or real payment rail. This is a synthetic burst study, not maximum capacity.</div>
<div class="stats"><div class="stat"><b id="control-on-time"></b><span>control payments correct within 2 seconds</span></div><div class="stat"><b id="candidate-on-time"></b><span>candidate payments correct within 2 seconds</span></div><div class="stat"><b id="goodput-change"></b><span>median paired change in correct on-time payments/s</span></div><div class="stat"><b id="correct"></b><span>measured original payments verified; warmup excluded</span></div></div>
<p id="p95-summary" class="metric"></p>
<div class="toolbar"><label for="pair">Matched pair</label><select id="pair"></select><label for="burst">Measured burst</label><select id="burst"></select><span>Every measured burst is selectable; the axis stays fixed.</span></div>
<div class="legend"><span><b style="background:#168158"></b>Correct and on time</span><span><b style="background:#bf5d1d"></b>Correct but late</span><span>│ Tick = that payment's deadline</span></div>
<p class="note">Each line starts at the scheduled arrival and ends when durable completion was observed. The completion observer polls every 100 ms in both arms. These are individual observations, not run percentiles.</p>
<div class="grid"><section class="panel"><h2>Reconstructed 100 ms success delay</h2><p class="flow">Fetch → execute → complete → wait 100 ms</p><p id="control-burst-count" class="metric"></p><svg id="control-chart" viewBox="0 0 640 520" role="img" aria-label="Control: twenty scheduled payments and their observed completion times"></svg><a id="control-evidence" href="#">Open original event evidence</a></section>
<section class="panel"><h2>Current idle-only backoff</h2><p class="flow">Fetch → execute → complete → fetch next task</p><p id="candidate-burst-count" class="metric"></p><svg id="candidate-chart" viewBox="0 0 640 520" role="img" aria-label="Candidate: twenty scheduled payments and their observed completion times"></svg><a id="candidate-evidence" href="#">Open original event evidence</a></section></div>
<p class="note"><b>Fixed controls:</b> one worker slot, 100 ms idle/error backoff, engine/JVM/database/resources, payment inputs, polling and instrumentation. Five pairs, with the same checkpoint restored before each arm. Each arm measures 120 seconds after 120 seconds of warmup. The rate denominator includes the quiet parts between bursts.</p>
<p class="foot">Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.<br><a href="summary.json">Independent audit and pair results</a> · <a href="https://github.com/oci-fsi-pursuits/fluxnova-benchmarking">Source and reproduction guide</a>. This page displays saved evidence and starts no workloads.</p>
<script>const DATA=__DATA__;
const C=DATA.summary.comparison,ctrl=C.arms.success_delay_100ms,cand=C.arms.idle_only;
const text=(id,value)=>document.getElementById(id).textContent=value;
text('control-on-time',ctrl.on_time_correct+' / '+ctrl.offered);text('candidate-on-time',cand.on_time_correct+' / '+cand.offered);text('goodput-change',(C.median_paired_deadline_goodput_difference_per_s>=0?'+':'')+C.median_paired_deadline_goodput_difference_per_s.toFixed(3)+'/s');text('correct',DATA.summary.measured_correct+' / '+DATA.summary.measured_offered);
text('p95-summary','Median run observed p95: '+ctrl.median_run_p95_ms.toFixed(1)+' → '+cand.median_run_p95_ms.toFixed(1)+' ms. Median paired change: '+C.median_paired_p95_change_percent.toFixed(1)+'%.');
const pair=document.getElementById('pair'),burst=document.getElementById('burst');for(let i=1;i<=5;i++){const o=document.createElement('option');o.value=i;o.textContent='Pair '+i;pair.appendChild(o)}for(let i=0;i<12;i++){const o=document.createElement('option');o.value=i;o.textContent='Burst '+(i+1);burst.appendChild(o)}
const NS='http://www.w3.org/2000/svg';function node(tag,attrs,label){const n=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);if(label!==undefined)n.textContent=label;return n}
function draw(id,rows){const svg=document.getElementById(id);svg.replaceChildren();const x=t=>52+t/DATA.axis_max_s*560;for(let t=0;t<=DATA.axis_max_s;t++){svg.appendChild(node('line',{x1:x(t),x2:x(t),y1:30,y2:475,class:'axis'}));svg.appendChild(node('text',{x:x(t),y:505,'text-anchor':'middle'},t+' s'))}svg.appendChild(node('text',{x:20,y:18},'Arrival'));for(const [i,r]of rows.entries()){const y=43+i*22;svg.appendChild(node('text',{x:20,y:y+4,'text-anchor':'middle'},String(i+1)));const mark=node('line',{x1:x(r.planned_s),x2:x(r.observed_s),y1:y,y2:y,class:'mark '+(r.on_time?'on-time':'late'),'data-index':i});mark.appendChild(node('title',{},'Payment '+(i+1)+': '+r.latency_s.toFixed(3)+' s; correct'+(r.on_time?' and on time':' but late')));svg.appendChild(mark);svg.appendChild(node('line',{x1:x(r.planned_s+2),x2:x(r.planned_s+2),y1:y-8,y2:y+8,class:'deadline'}))}}
function show(){const p=DATA.pairs[Number(pair.value)-1];for(const [prefix,arm]of [['control','success_delay_100ms'],['candidate','idle_only']]){const r=p[arm],rows=r.bursts[Number(burst.value)];draw(prefix+'-chart',rows);const n=rows.filter(r=>r.on_time).length;text(prefix+'-burst-count',n+' / 20 on time · '+(20-n)+' late');document.getElementById(prefix+'-evidence').href=r.evidence}}
pair.addEventListener('change',show);burst.addEventListener('change',show);show();</script></main></html>'''


def build(source,output):
    if output.exists():raise ValueError('Use a fresh output folder.')
    summary=recheck(source,'campaign')
    if summary['status']!='complete' or not summary['comparison']:raise ValueError('A complete independently audited ten-arm campaign is required.')
    plan=json.loads((source/'measurement-plan.json').read_text());pairs=[];maximum=3.0
    output.mkdir(parents=True);(output/'evidence').mkdir()
    for index,arms in enumerate(plan['pairs'],1):
        pair={}
        for order,arm in enumerate(arms,1):
            path=source/f'p{index}-{order}-{arm}'/'events.ndjson'
            events=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            bursts=[]
            for burst in range(12):
                rows=[r for r in events if r['event']=='completed' and r['phase']=='measurement'
                      and r['logical_request_id'].rsplit(':',2)[-2]==str(burst)]
                rows.sort(key=lambda r:r['scheduled_elapsed_s']);assert len(rows)==20
                values=[{'planned_s':r['scheduled_elapsed_s']-burst*10,'observed_s':r['elapsed_s']-burst*10,
                         'latency_s':r['latency_s'],'on_time':r['latency_s']<=2} for r in rows]
                maximum=max(maximum,max(r['observed_s'] for r in values));bursts.append(values)
            name=f'pair-{index}-{arm}.ndjson';shutil.copyfile(path,output/'evidence'/name)
            pair[arm]={'bursts':bursts,'evidence':'evidence/'+name,'original_events_sha256':sha(path)}
        pairs.append(pair)
    data={'summary':summary,'pairs':pairs,'axis_max_s':int(maximum)+1}
    (output/'index.html').write_text(HTML.replace('__DATA__',json.dumps(data).replace('<','\\u003c')),encoding='utf-8')
    (output/'data.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    (output/'manifest.json').write_text(json.dumps({'scope':'Saved W01 burst measurements; no new workload executions',
        'builder_sha256':sha(Path(__file__)),'files':{p.relative_to(output).as_posix():sha(p) for p in output.rglob('*') if p.is_file()}},indent=2)+'\n')
    archive=output.with_suffix('.zip');assert not archive.exists()
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for p in output.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(output).as_posix())
    print(json.dumps({'status':'built','measured_payments':summary['measured_correct'],'paired_bursts':60,'viewer':str(output/'index.html')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build(args.source.resolve(),args.output.resolve())
