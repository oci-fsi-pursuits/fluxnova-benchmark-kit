"""Saved W02 functional evidence view. No invented benchmark metrics or live counters."""
from html import escape
import json
from pathlib import Path
import textwrap
import xml.etree.ElementTree as ET

NS = {'b':'http://www.omg.org/spec/BPMN/20100524/MODEL','bd':'http://www.omg.org/spec/BPMN/20100524/DI',
      'dc':'http://www.omg.org/spec/DD/20100524/DC','di':'http://www.omg.org/spec/DD/20100524/DI'}


def diagram(path):
    root = ET.parse(path).getroot()
    elements = {e.get('id'):e for e in root.iter() if e.get('id')}
    parts = ['<svg viewBox="20 25 780 330" role="img" aria-label="'+escape(path.stem)+' BPMN workflow">',
             '<defs><marker id="arrow-'+path.stem+'" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6" fill="none" stroke="#718096"/></marker></defs>']
    for edge in root.findall('.//bd:BPMNEdge',NS):
        points = ' '.join(p.get('x')+','+p.get('y') for p in edge.findall('di:waypoint',NS))
        parts.append('<polyline points="'+points+'" fill="none" stroke="#718096" stroke-width="1.5" marker-end="url(#arrow-'+path.stem+')"/>')
    for shape in root.findall('.//bd:BPMNShape',NS):
        node = elements[shape.get('bpmnElement')]
        b = shape.find('dc:Bounds',NS)
        x,y,w,h = [float(b.get(k)) for k in ('x','y','width','height')]
        cx,cy = x+w/2,y+h/2
        kind = node.tag.rsplit('}',1)[-1]
        if kind.endswith('Gateway'):
            parts.append(f'<polygon points="{cx},{y} {x+w},{cy} {cx},{y+h} {x},{cy}" fill="#f0f9ff" stroke="#2870a8"/>')
            symbol = '+' if kind=='parallelGateway' else '×'
            parts.append(f'<text x="{cx}" y="{cy+6}" text-anchor="middle" font-size="22">{symbol}</text>')
        elif kind.endswith('Event'):
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{w/2}" fill="white" stroke="#1d7765" stroke-width="'+('3' if kind=='endEvent' else '1.5')+'"/>')
        else:
            parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="#f3f8fd" stroke="#93b6ce"/>')
        label = node.get('name','')
        lines = textwrap.wrap(label,width=18)
        label_y = cy-((len(lines)-1)*7)+4 if kind=='serviceTask' else y+h+15
        for i,line in enumerate(lines):
            parts.append(f'<text x="{cx}" y="{label_y+i*14}" text-anchor="middle" font-size="11">{escape(line)}</text>')
    return ''.join(parts)+'</svg>'


def write_report(folder):
    folder = Path(folder)
    summary = json.loads((folder/'summary.json').read_text())
    manifest = json.loads((folder/'manifest.json').read_text())
    rows = ''.join('<tr data-arm="'+escape(r['arm'])+'"><td>'+escape(r['case_id'].replace('_',' '))+'</td><td>'+r['arm']+'</td><td class="passed">'+r['status']+'</td><td>'+r['outcome']+'</td><td>'+str(r['effects'])+'</td><td>'+('Yes' if r['checks_overlap'] else 'No')+'</td><td><a href="'+escape(r['arm']+'-'+r['case_id']+'.json')+'">Evidence</a></td></tr>' for r in summary['cases'])
    figures = ''.join('<section class="diagram" data-arm="'+arm+'"><h2>'+arm.title()+' checks</h2>'+diagram(folder/'inputs/workloads/w02'/ (arm+'.bpmn'))+'</section>' for arm in ('sequential','parallel'))
    replay_probes = sum(p['status'] == 'blocked_as_expected' for p in summary['replay_probes'])
    html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fluxnova W02 functional comparison</title><style>
*{box-sizing:border-box}body{font:16px/1.5 system-ui,sans-serif;background:#f4f7fb;color:#172b3a;margin:0}main{max-width:1180px;margin:auto;padding:34px}h1{font-size:30px;line-height:1.2;margin:8px 0 14px}h2{font-size:19px;margin:0 0 6px}.eyebrow{font-size:12px;font-weight:750;letter-spacing:1.3px;color:#24669a}.scope{font-weight:650;color:#9a471a}.lead{max-width:920px}.controls{margin:24px 0;display:flex;gap:8px;align-items:center}button{font:inherit;padding:8px 18px;background:white;border:1px solid #a9bbce;border-radius:6px;cursor:pointer}button[aria-pressed=true]{background:#075b96;color:white}.figures{display:grid;grid-template-columns:1fr 1fr;gap:18px}.diagram{background:white;border:1px solid #dae4ed;border-radius:10px;padding:18px}.diagram svg{width:100%;height:auto;font-family:system-ui,sans-serif}.facts{margin:20px 0;color:#455b6d}.results{background:white;padding:20px;border-radius:10px;border:1px solid #dae4ed;overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;background:#e9f1f7}td,th{padding:10px 11px;border-bottom:1px solid #dbe5ee}.passed{color:#087057;font-weight:650}a{color:#075f99}footer{margin-top:20px;color:#596d7c;font-size:13px}[hidden]{display:none!important}@media(max-width:800px){main{padding:18px}.figures{grid-template-columns:1fr}h1{font-size:25px}}
</style><main><div class="eyebrow">FLUXNOVA RESEARCH / W02</div><h1>Independent payment checks<br>Sequential and parallel workflows</h1>
<p class="scope">LOCAL FUNCTIONAL VALIDATION - NOT OCI PERFORMANCE</p>
<p class="lead">Both workflows use the same original payment inputs, dependency schedule and two worker slots. The parallel workflow joins both checks before the backend can create an exact synthetic payment effect. This view reads saved engine history and independent backend evidence.</p>
<div class="controls" role="group" aria-label="Filter workflow arm"><button data-filter="both" aria-pressed="true">Both workflows</button><button data-filter="sequential" aria-pressed="false">Sequential</button><button data-filter="parallel" aria-pressed="false">Parallel</button></div>
<div class="figures">'''+figures+'''</div><p class="facts"><b>'''+str(summary.get('functional_cases_passed',0))+''' functional cases passed.</b> Repeated operations preserved the original effects. '''+str(replay_probes)+''' cross-process replay probes were blocked. Parallel overlap is a functional check, not a measured throughput or speedup claim.</p>
<section class="results"><h2>Recorded outcomes</h2><table><thead><tr><th>Case</th><th>Workflow</th><th>Check</th><th>Outcome</th><th>Effects</th><th>Check overlap</th><th>Saved record</th></tr></thead><tbody>'''+rows+'''</tbody></table></section>
<footer>Windows / Java 21 / embedded H2. Dependency timings are synthetic. No steady-state load campaign, complete cost allocation or OCI performance result. The owned engine was stopped after verification. This is a saved evidence viewer, not a live run launcher.<br><a href="summary.json">Summary</a> · <a href="manifest.json">Exact input and runtime inventory</a> · <a href="checksums.json">Evidence checksums</a></footer>
<script>document.querySelectorAll('button[data-filter]').forEach(button=>button.addEventListener('click',()=>{const value=button.dataset.filter;document.querySelectorAll('button[data-filter]').forEach(b=>b.setAttribute('aria-pressed',b===button));document.querySelectorAll('[data-arm]').forEach(row=>row.hidden=value!=='both'&&row.dataset.arm!==value);document.querySelector('.figures').style.gridTemplateColumns=value==='both'?'':'1fr'}));</script></main></html>'''
    if manifest.get('study_id') == 'w02_s1':
        html = html.replace('Fluxnova W02 functional comparison','Fluxnova W02 dependency sensitivity')
        html = html.replace('FLUXNOVA RESEARCH / W02','FLUXNOVA RESEARCH / W02-S1')
        html = html.replace('Independent payment checks<br>Sequential and parallel workflows',escape(manifest['title']))
        profile_rows = ''.join('<tr><td>'+escape(p['label'])+'</td><td>'+str(p['kyc_ms'])+'</td><td>'+str(p['risk_ms'])+'</td></tr>' for p in manifest['profiles'])
        grid = '<section class="results"><h2>Prespecified dependency profiles</h2><p>Each profile runs all ten policy and fault cases in both workflows. Equal coverage is a test design, not a customer frequency distribution. These per-case runs do not measure p95 or sustainable throughput.</p><table><thead><tr><th>Profile</th><th>KYC wait, ms</th><th>Risk wait, ms</th></tr></thead><tbody>'+profile_rows+'</tbody></table></section>'
        html = html.replace('<div class="controls"',grid+'<div class="controls"',1)
    (folder/'report.html').write_text(html,encoding='utf-8')
    return folder/'report.html'
