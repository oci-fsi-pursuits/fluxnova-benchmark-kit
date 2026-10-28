#!/usr/bin/env python3
"""Build a portable W03 viewer from audited, checksum-verified saved executions."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benchlib.review_routing_report import audit_record


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


HTML='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>W03 · Review where policy requires it</title>
<style>
:root{font-family:Arial,sans-serif;color:#173e38;background:#edf2ef}*{box-sizing:border-box}body{margin:0}main{max-width:1400px;margin:auto;padding:32px}h1{font-size:42px;margin:10px 0 14px}h2{font-size:22px;margin:0 0 18px}p{line-height:1.5}.eyebrow{font-size:14px;letter-spacing:1px;font-weight:700;color:#466a61}.lead{font-size:20px;max-width:1000px}.scope{border-left:5px solid #d27c23;background:#fff5e6;padding:14px 18px;line-height:1.5}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}.stat,.panel{background:white;border:1px solid #cad7d1;border-radius:12px;padding:22px}.stat b{display:block;font-size:34px;margin-bottom:7px}.stat span{font-size:16px}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:26px 0}label{font-weight:bold}select,button{font-size:17px;padding:12px;border:1px solid #557f73;border-radius:7px;background:white;color:#173e38}button{cursor:pointer}button[aria-pressed=true]{background:#173e38;color:white}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #1375dd;outline-offset:3px}.grid{display:grid;grid-template-columns:1.25fr 1fr;gap:20px}.panel svg{width:100%;height:auto;display:block}dl{display:grid;grid-template-columns:150px 1fr;gap:13px;margin:0}dt{font-weight:bold}dd{margin:0;overflow-wrap:anywhere}.outcome{padding:16px;border-radius:8px;background:#e0f4e7;margin:20px 0;font-size:21px;font-weight:bold}.evidence{font-size:16px;line-height:1.5;color:#365e53}.foot{margin-top:22px;font-size:14px;line-height:1.5}a{color:#075cac}.muted{color:#536e65}.note{font-size:16px;line-height:1.5}svg text{font-family:Arial,sans-serif;font-size:17px;fill:#173e38}.node{fill:#edf5f0;stroke:#4b7b6b;stroke-width:2}.active{fill:#c7ebdc;stroke:#087955;stroke-width:3}.line{fill:none;stroke:#466c60;stroke-width:2;marker-end:url(#arrow)}[hidden]{display:none!important}@media(max-width:950px){.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}main{padding:22px}h1{font-size:34px}}
</style><main>
<div class="eyebrow">FLUXNOVA BENCHMARKING · W03</div>
<h1>Review where policy requires it</h1>
<p class="lead">Keep the original payment, authorization rules and expected outcome fixed. Change which cases need a reviewer.</p>
<div class="scope"><b>Saved synthetic executions.</b> The all-review control is a declared demonstration. Policy routing uses deterministic rules. No live model, new payment or measured human time saving is implied.</div>
<div class="stats"><div class="stat"><b>10 → 4</b><span>review tasks in the same ten-case corpus</span></div><div class="stat"><b>4 / 4</b><span>mandatory reviews retained in the candidate</span></div><div class="stat"><b>20 / 20</b><span>expected outcomes across both arms</span></div><div class="stat"><b>8 exact</b><span>synthetic effects; four per arm</span></div></div>
<div class="toolbar"><label for="case">Original payment case</label><select id="case"></select><button id="all_review" aria-pressed="true">All-review control</button><button id="policy_routing" aria-pressed="false">Policy routing</button></div>
<div class="grid"><section class="panel"><h2 id="graph-title"></h2>
<svg viewBox="0 0 670 420" role="img" aria-label="Simplified executed workflow; required review precedes verification and any effect">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#466c60"/></marker></defs>
<rect class="node" x="20" y="30" width="200" height="70" rx="10"/><text x="120" y="61" text-anchor="middle">Original payment</text><text x="120" y="85" text-anchor="middle">bound to this process</text>
<path class="line" d="M220 65 H300"/><rect class="node" x="300" y="30" width="340" height="70" rx="10"/><text x="470" y="61" text-anchor="middle">Evaluate the fixed policy</text><text id="policy-label" x="470" y="85" text-anchor="middle"></text>
<g id="review-path"><path class="line" d="M470 100 V150"/><rect id="review-box" class="node active" x="300" y="150" width="340" height="80" rx="10"/><text x="470" y="180" text-anchor="middle">Separately authenticated review</text><text id="review-label" x="470" y="207" text-anchor="middle"></text><path class="line" d="M470 230 V290"/></g>
<g id="direct-path" hidden><path class="line" d="M470 100 V290"/><rect x="365" y="169" width="210" height="55" rx="6" fill="white"/><text x="470" y="193" text-anchor="middle">No review required</text><text x="470" y="216" text-anchor="middle">for this case</text></g>
<rect class="node active" x="300" y="290" width="340" height="80" rx="10"/><text x="470" y="321" text-anchor="middle">Verify original inputs and actor</text><text x="470" y="346" text-anchor="middle">Then allow the exact outcome</text>
<text x="20" y="320">Authority cannot be overridden.</text><text x="20" y="347">Unknown checks block payment.</text><text x="20" y="374">Repeats cannot add an effect.</text>
</svg><p class="note">Simplified view of the selected BPMN and saved execution. <a id="bpmn" href="#">Open its BPMN source</a>.</p></section>
<section class="panel"><h2>Same intent. Verified outcome.</h2><dl><dt>Payment</dt><dd id="payment"></dd><dt>Original amount</dt><dd id="amount"></dd><dt>Fixed policy</dt><dd id="policy"></dd><dt>Review tasks</dt><dd id="reviews"></dd><dt>Review evidence</dt><dd id="review-evidence"></dd></dl><div class="outcome" id="outcome"></div><p id="effects" class="evidence"></p><p class="note">The harness pauses 300 ms before scripted review. Per-case durations are illustrative; active human handling time and human time saved were not measured.</p><a id="evidence" href="#">Open the original execution evidence (JSON)</a></section></div>
<p class="foot">Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.<br>Local Fluxnova 3.0.1/H2 functional study. <a href="https://github.com/oci-fsi-pursuits/fluxnova-benchmarking">Source and reproduction guide</a>. This viewer makes no network requests.</p>
<script>const DATA=__DATA__;
let arm='all_review';const sel=document.querySelector('#case');for(const name of DATA.case_order){const o=document.createElement('option');o.value=name;o.textContent=name.replaceAll('_',' ');sel.appendChild(o)}
function show(){const r=DATA.records.find(r=>r.case_id===sel.value&&r.arm===arm);for(const a of ['all_review','policy_routing'])document.getElementById(a).setAttribute('aria-pressed',String(a===arm));document.getElementById('graph-title').textContent=arm==='all_review'?'All-review control':'Policy routing';document.getElementById('policy-label').textContent=r.policy.replaceAll('_',' ');document.getElementById('review-path').toggleAttribute('hidden',!r.review_tasks);document.getElementById('direct-path').toggleAttribute('hidden',!!r.review_tasks);document.getElementById('review-label').textContent=r.review_decision||'';document.getElementById('payment').textContent=r.payment.payment_id;document.getElementById('amount').textContent=r.payment.amount_minor+' minor units → '+r.payment.beneficiary;document.getElementById('policy').textContent=r.policy.replaceAll('_',' ');document.getElementById('reviews').textContent=String(r.review_tasks);document.getElementById('review-evidence').textContent=r.review_tasks?'Completed by the registered reviewer; actual engine operation log verified.':'No reviewer task; the same policy and exact-effect verifier still apply.';document.getElementById('outcome').textContent=r.outcome.replaceAll('_',' ');document.getElementById('effects').textContent=r.effects+' exact synthetic effect'+(r.effects===1?'':'s')+'. Six repeated calls preserved the original receipts and effects. Cross-process replay was refused.';document.getElementById('evidence').href=r.evidence;document.getElementById('bpmn').href='workloads/'+arm+'.bpmn';}
sel.addEventListener('change',show);for(const a of ['all_review','policy_routing'])document.getElementById(a).addEventListener('click',()=>{arm=a;show()});show();</script></main></html>'''


def build(source,output):
    if output.exists():raise ValueError('Use a new output directory.')
    manifest=json.loads((source/'checksums.json').read_text())
    for name,wanted in manifest['files'].items():
        path=(source/name).resolve();assert path.is_relative_to(source.resolve()) and sha(path)==wanted
    summary=json.loads((source/'summary.json').read_text())
    assert summary['status']=='passed' and summary['owned_engine_stopped'] and not summary['cleanup_errors']
    assert summary['functional_cases_passed']==20 and summary['review_tasks']=={'all_review':10,'policy_routing':4}
    output.mkdir(parents=True);(output/'evidence').mkdir();(output/'workloads').mkdir()
    corpus=json.loads((source/'inputs/workloads/w03/cases.json').read_text());records=[]
    for case in corpus['cases']:
        for arm in ('all_review','policy_routing'):
            name=arm+'-'+case['case_id']+'.raw.json';path=source/name;r=json.loads(path.read_text());checked=audit_record(r)
            replay=json.loads((source/name.replace('.raw.','.replay.')).read_text())
            assert replay['before']['effects']==replay['after']['effects'] and replay['before']['receipts']==replay['after']['receipts']
            assert replay['repeated_calls']==6 and replay['cross_process_replay']=='rejected'
            shutil.copyfile(path,output/'evidence'/name)
            records.append({**checked,'payment':case['payment'],'policy':r['backend']['receipts']['route']['outcome'],
                'review_decision':case['scripted_reviewer_decision'] if checked['review_tasks'] else None,
                'evidence':'evidence/'+name,'evidence_sha256':sha(path)})
    for arm in ('all_review','policy_routing'):shutil.copyfile(source/'inputs/workloads/w03'/(arm+'.bpmn'),output/'workloads'/(arm+'.bpmn'))
    data={'case_order':[c['case_id'] for c in corpus['cases']],'records':records}
    (output/'index.html').write_text(HTML.replace('__DATA__',json.dumps(data).replace('<','\\u003c')),encoding='utf-8')
    (output/'data.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    (output/'manifest.json').write_text(json.dumps({'scope':'Saved W03 functional executions; no new workloads',
        'source_checksums_sha256':sha(source/'checksums.json'),'builder_sha256':sha(Path(__file__)),
        'files':{p.relative_to(output).as_posix():sha(p) for p in output.rglob('*') if p.is_file()}},indent=2)+'\n')
    archive=output.with_suffix('.zip');assert not archive.exists()
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for p in output.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(output).as_posix())
    print(json.dumps({'status':'built','cases':len(records),'viewer':str(output/'index.html'),'browser_review':'pending'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build(args.source.resolve(),args.output.resolve())
