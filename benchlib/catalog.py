"""Versioned research catalogue. Reading it never launches a workload."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path, PurePosixPath
from .util import ROOT

CATALOG = Path('workloads/benchmark-catalog.json')
STUDY_FIELDS = ('id', 'name', 'type', 'status', 'claim_level', 'question', 'control',
                'change', 'held_fixed', 'population', 'environment', 'repeats',
                'metric', 'clock', 'result', 'meaning', 'limits', 'reproduce', 'evidence')
CLAIMS = {'measured', 'functional', 'partial', 'diagnostic', 'sample'}


def load_catalog(root=ROOT):
    return json.loads((Path(root) / CATALOG).read_text(encoding='utf-8'))


def _file(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Catalogue paths must be repository-relative')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('Catalogue path escapes the repository')
    target = (Path(root) / name).resolve()
    if not target.is_relative_to(Path(root).resolve()) or not target.is_file():
        raise ValueError('Missing or unsafe catalogue file: ' + name)
    return target


def validate_catalog(data, root=ROOT):
    """Fail closed on missing evidence, changed files or misleading categories.

    This checks catalogue integrity and its claim contract, not the correctness
    of an experiment. Each study still needs its independent outcome audit.
    """
    if data.get('schema_version') != 1 or data.get('protocol_version') != '0.2':
        raise ValueError('Unsupported catalogue or protocol version')
    if data.get('standard_status') != 'community_proposal':
        raise ValueError('This kit is a community proposal, not an adopted standard')
    types = data.get('types', {})
    ids = set()
    for s in data.get('studies', []):
        if any(not s.get(k) for k in STUDY_FIELDS):
            raise ValueError('Incomplete study record: ' + s.get('id', '?'))
        if s['id'] in ids or s['type'] not in types or s['claim_level'] not in CLAIMS:
            raise ValueError('Duplicate ID or unknown study classification')
        ids.add(s['id'])
        if s['claim_level'] not in types[s['type']]['allowed_claim_levels']:
            raise ValueError('Claim exceeds benchmark type: ' + s['id'])
        if s['claim_level'] == 'measured' and s['repeats'] == 'not applicable':
            raise ValueError('Measured studies must declare their observations')
        _file(root, s['reproduce'])
        for e in s['evidence']:
            content = _file(root, e['path']).read_bytes()
            if hashlib.sha256(content).hexdigest() != e['sha256']:
                raise ValueError('Evidence changed: ' + e['path'])
    if not ids:
        raise ValueError('Empty study catalogue')
    pattern_ids = set()
    images = set()
    for p in data.get('patterns', []):
        if any(not p.get(k) for k in ('id', 'name', 'image', 'question', 'explanation', 'interpretation', 'source', 'studies')):
            raise ValueError('Incomplete pattern record')
        if p['id'] in pattern_ids or p['image'] in images:
            raise ValueError('Duplicate pattern or image')
        pattern_ids.add(p['id'])
        images.add(p['image'])
        _file(root, p['image'])
        _file(root, p['source'])
        if any(i not in ids for i in p['studies']):
            raise ValueError('Pattern refers to an unknown study')
    if len(images) != data.get('illustrated_pattern_count'):
        raise ValueError('Illustrated pattern inventory is incomplete')
    return {'status': 'passed', 'protocol_version': data['protocol_version'],
            'studies': len(ids), 'illustrated_patterns': len(images),
            'scope': 'Catalogue integrity and classification; not a new benchmark run'}


def explain(identifier, data=None):
    data = data or load_catalog()
    for section in ('studies', 'patterns'):
        for item in data[section]:
            if item['id'].casefold() == identifier.casefold():
                return item
    raise ValueError('Unknown catalogue ID: ' + identifier)


def markdown(data):
    lines = ['# What the Fluxnova benchmarks measure', '', data['thesis'], '',
             'This guide is generated from the [versioned catalogue](../workloads/benchmark-catalog.json). '
             'Research and OCI implementation: **Nicholas Cusato**. Fluxnova software: **FINOS contributors**.', '',
             '## The simplest explanation', '',
             'A workflow developer chooses a reusable BPMN pattern and test inputs. The harness starts workflows, '
             'workers perform the declared tasks, and an independent checker compares the result with the original input. '
             'A comparison repeats the same experiment with one declared change. The business check matters: '
             'a quick response that pays the wrong amount is a failed test.', '',
             '**A pattern is what runs. A benchmark type is how we test it. A study is one frozen experiment.** '
             '“Agentic” describes a workload that uses an agent; it does not by itself mean we measured AI quality or benefit.', '',
             '## Types of benchmarking', '', '| Type | Question it answers |', '| --- | --- |']
    for t in data['types'].values():
        lines.append(f"| {t['name']} | {t['question']} |")
    lines += ['', '## How to read the results', ''] + ['- ' + x for x in data['reading_rules']]
    lines += ['', '## Study register', '', '| Study | Type and evidence level | Result |', '| --- | --- | --- |']
    for s in data['studies']:
        lines.append(f"| [{s['id']}: {s['name']}](#{s['id'].lower()}) | {data['types'][s['type']]['name']}; {s['claim_level']} | {s['result']} |")
    for s in data['studies']:
        lines += ['', f"## {s['id']}", '', f"**{s['name']}**. {s['question']}", '',
                  f"**Evidence:** {s['status']}. {data['types'][s['type']]['name']}; {s['claim_level']}.", '',
                  f"**Control and change:** {s['control']} → {s['change']}", '',
                  f"**Held fixed:** {s['held_fixed']}", '',
                  f"**Test:** {s['population']} {s['repeats']} {s['environment']}", '',
                  f"**Metric and clock:** {s['metric']} {s['clock']}", '',
                  f"**Result:** {s['result']}", '', f"**What it means:** {s['meaning']}", '',
                  f"**Boundary:** {s['limits']}", '',
                  f"[Reproduction route]({Path(s['reproduce']).name if s['reproduce'].startswith('docs/') else '../'+s['reproduce']}). " +
                  ' '.join(f"[Evidence {i+1}](../{e['path']})" for i,e in enumerate(s['evidence']))]
    lines += ['', '## All nine diagrammed patterns', '',
              'The six basic patterns are building blocks. W01 and W02 add timed comparisons. W03 compares review counts. '
              'These nine images do not represent nine completed performance studies.', '']
    for p in data['patterns']:
        lines += [f"### {p['id']}: {p['name']}", '', f"![{p['question']}](../{p['image']})", '',
                  p['explanation'], '', '**What the result means:** '+p['interpretation'], '',
                  f"[Executable source or guide](../{p['source']}). Related studies: " + ', '.join(f"[{i}](#{i.lower()})" for i in p['studies'])+'.', '']
    lines += ['## Reuse and contribution', '',
              'Use a source checkout. `python -m benchlib.cli catalog --check` validates this register. '
              '`python -m benchlib.cli explain W02` shows one full experiment contract. '
              'The [supported reproduction paths](benchmark-standard.md) separate sample reports, local functional execution '
              'and measured cloud campaigns.', '',
              'The protocol is a community contribution proposal. It is not an adopted FINOS standard. '
              'Open workloads and containers can be adapted to other clouds and on premises. Provisioning, identity, '
              'telemetry and cost integrations currently target OCI and require platform-specific validation elsewhere.', '']
    return '\n'.join(lines)


def html_guide(data):
    """A self-contained navigation page with relative image/evidence links."""
    e = html.escape
    out = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>Fluxnova benchmark guide</title><style>body{margin:0;color:#073b59;font:18px/1.55 Arial;background:#fff}main{max-width:1100px;margin:auto;padding:35px}h1{font-size:40px;line-height:1.13}h2{margin-top:40px}a{color:#006d91}label{display:block}input{font:inherit;padding:10px;width:95%;max-width:650px;border:1px solid #517889}article{border-top:2px solid #bddae4;margin-top:30px;padding-top:15px}img{width:100%;height:auto}dt{font-weight:bold;margin-top:12px}dd{margin-left:0}nav a{display:inline-block;margin:5px 20px 5px 0}header{background:#e9f7fb;padding:25px}.muted{color:#425b68}[hidden]{display:none}@media print{input,label,nav{display:none}article{break-inside:avoid}h1{font-size:28px}}</style>',
           '<main><header><p>Research by Nicholas Cusato · Fluxnova software by FINOS contributors</p><h1>What the Fluxnova benchmarks measure</h1><p>'+e(data['thesis'])+'</p></header>',
           '<p>A <strong>pattern</strong> is what runs. A <strong>benchmark type</strong> is how we test it. A <strong>study</strong> is one frozen experiment.</p>',
           '<p>Choose a workflow and known inputs. Run it. Verify the final outcome independently. Repeat with one declared change and compare against its own baseline.</p>',
           '<nav><a href="benchmark-standard.md">Supported run paths</a><a href="benchmark-guide.md">Text and sources</a><a href="w02-saved-demo.md">Demo instructions</a></nav>',
           '<label for="find">Find a pattern, result or benchmark type</label><input id="find" type="search" placeholder="Try parallel, Java, review, soak…"><p id="count" role="status"></p>',
           '<h2>What each type answers</h2><dl>']
    for t in data['types'].values():
        out += ['<dt>'+e(t['name'])+'</dt><dd>'+e(t['question'])+'</dd>']
    out += ['</dl><h2>Reading the results</h2><ul>'] + ['<li>'+e(x)+'</li>' for x in data['reading_rules']] + ['</ul>']
    for s in data['studies']:
        out += ['<article id="'+e(s['id'])+'"><h2>'+e(s['id']+' · '+s['name'])+'</h2><p class="muted">'+e(data['types'][s['type']]['name']+' / '+s['claim_level']+' / '+s['status'])+'</p>',
                '<h3>'+e(s['result'])+'</h3><p>'+e(s['meaning'])+'</p><details><summary>What changed and how we tested it</summary><dl>']
        for label,key in [('Question','question'),('Control','control'),('Change','change'),('Held fixed','held_fixed'),('Population','population'),('Repeats','repeats'),('Environment','environment'),('Metric','metric'),('Clock','clock')]:
            out += ['<dt>'+label+'</dt><dd>'+e(s[key])+'</dd>']
        out += ['</dl></details><p><strong>Boundary:</strong> '+e(s['limits'])+'</p><p>']
        out += ['<a href="../'+e(x['path'])+'">Evidence '+str(i+1)+'</a> ' for i,x in enumerate(s['evidence'])]
        out += ['<a href="../'+e(s['reproduce'])+'">Reproduction</a></p></article>']
    out += ['<h2>All nine diagrammed patterns</h2><p>Six basic building blocks, two timed workflow comparisons and one functional review comparison. Conceptual flowcharts explain the executable BPMN.</p>']
    for p in data['patterns']:
        out += ['<article id="'+e(p['id'])+'"><h2>'+e(p['id']+' · '+p['name'])+'</h2><img loading="lazy" src="../'+e(p['image'])+'" alt="'+e(p['question'])+'"><p>'+e(p['explanation'])+'</p><p><strong>Meaning:</strong> '+e(p['interpretation'])+'</p><p>']
        out += ['<a href="#'+e(i)+'">'+e(i)+'</a> ' for i in p['studies']]
        out += ['</p></article>']
    out += ['<footer><p>Protocol 0.2 community proposal. No FINOS certification. Other cloud and on-premises adapters require validation. No real money moves in these synthetic tests.</p></footer></main>',
            '<script>const q=document.getElementById("find"),a=[...document.querySelectorAll("article")],c=document.getElementById("count");function filter(){const s=q.value.toLowerCase();a.forEach(x=>x.hidden=!x.textContent.toLowerCase().includes(s));c.textContent=a.filter(x=>!x.hidden).length+" matching entries"}q.addEventListener("input",filter);filter();</script></html>']
    return '\n'.join(line.rstrip() for line in out)+'\n'
