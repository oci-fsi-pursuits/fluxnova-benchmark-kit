#!/usr/bin/env python3
"""Copy verified talk artifacts and sealed demos into a portable offline folder."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]

PAGE='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fluxnova | Presenter launchpad</title><style>
:root{--ink:#093c57;--cyan:#00afd2;--line:#d0e2e8}*{box-sizing:border-box}body{margin:0;background:#f1f6f8;color:var(--ink);font:17px/1.5 Arial,sans-serif}header{background:var(--ink);color:white;padding:40px max(24px,calc((100% - 1120px)/2))}header p{margin:0;color:#b9e8ef}h1{font-size:38px;line-height:1.15;max-width:1050px;margin:12px 0}main{max-width:1168px;margin:auto;padding:30px 24px}h2{font-size:24px;margin:0 0 12px}h3{font-size:19px;margin:0 0 8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:24px}.files{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0 28px}.button{display:inline-block;background:var(--ink);color:white;text-decoration:none;padding:11px 17px;border-radius:7px;font-weight:700}.primary{background:#007b98}a{color:#006e9a}a:focus-visible,video:focus-visible{outline:3px solid #e69a1b;outline-offset:3px}p{margin:10px 0 16px}.small{font-size:14px;color:#4b6675}video{width:100%;background:#102a35;border-radius:8px}.steps{padding-left:23px}li{margin:10px 0}.extras{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:22px}footer{font-size:13px;margin-top:24px;color:#4b6675}@media(max-width:780px){h1{font-size:29px}.grid,.extras{grid-template-columns:1fr}header{padding:28px 24px}}
</style><header><p>OSFF NEW YORK 2026 · PRESENTER LAUNCHPAD</p><h1>Benchmarking Fluxnova for Financial and Agentic Payment Workflows</h1><p>Timely, correct, authorized outcomes.</p></header><main>
<h2>Ready for a 15-minute talk</h2><p>One folder for your slides, handout and saved demos. Extract the ZIP, then open this page in Chrome or Edge. No sign-in or cloud connection is needed.</p>
<div class="files"><a class="button" href="slides/talk.pptx">Open the 10-slide talk</a><a class="button" href="slides/summary.pptx">Summary slide</a><a class="button" href="slides/team-brief.pdf">One-page team brief</a></div>
<div class="grid"><section class="card"><h2>The 75-second payment demo</h2><p>Follow the same payment through sequential and parallel checks, then show that missing authority or an unknown check prevents payment.</p><a class="button primary" id="main-demo" href="demos/w02/index.html">Open the payment-check demo</a><ol class="steps"><li><b>0–20 s:</b> Approved payment, sequential checks.</li><li><b>20–45 s:</b> Switch to parallel checks; point to overlapping receipts.</li><li><b>45–65 s:</b> Show unauthorized and unknown-KYC cases: zero effects.</li><li><b>65–75 s:</b> Point to repeat-call evidence, then return to the closing QR slide.</li></ol><p class="small">The viewer shows saved functional executions. The separate five-pair OCI study supplies the 32.1% p95 result.</p></section>
<section class="card"><h2>Recording fallback</h2><video id="walkthrough" controls preload="metadata" aria-label="Saved payment workflow walkthrough" src="video/w02-saved-walkthrough.webm"></video><p class="small">Reviewed saved walkthrough. Playback and the interactive viewers execute no new payments or model calls.</p><a href="video/w02-saved-walkthrough.webm">Open the recording full screen</a></section></div>
<div class="extras"><section class="card"><h3>Worker pacing under bursts</h3><p>Inspect 60 matched bursts and the two-second deadline for each payment.</p><a id="w01-demo" href="demos/w01/index.html">Open W01 results viewer</a></section><section class="card"><h3>Policy-based review routing</h3><p>Compare review tasks and verify that required reviews and intended outcomes remain.</p><a id="w03-demo" href="demos/w03/index.html">Open W03 results viewer</a></section></div>
<footer>Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.<br>13 minutes planned + 2 minutes reserve. Check legibility and playback on the actual presentation screen.<br><a href="manifest.json">Package file identities</a> · <a href="https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/main/docs/reproduce.md">Source and reproduction guide</a></footer></main></html>'''


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def build(config_path,output):
    config=read(config_path);output=output.resolve();archive=output.with_suffix('.zip')
    if output.exists() or archive.exists():raise ValueError('Use a new package path.')
    def source(name):
        p=(ROOT/name).resolve()
        if not p.is_relative_to(ROOT):raise ValueError('Source must be inside the repository.')
        return p
    record=read(source(config['presentation_record']))
    copies=[]
    for target,name in config['artifacts'].items():
        if target not in ('talk.pptx','summary.pptx','team-brief.pdf'):raise ValueError('Unexpected presentation artifact.')
        p=source(name)
        if sha(p)!=record['files'][name]:raise ValueError('Presentation artifact changed.')
        copies.append((p,'slides/'+target))
    if set(config['artifacts'])!={'talk.pptx','summary.pptx','team-brief.pdf'}:raise ValueError('Missing talk artifact.')
    demo_sources={}
    if set(config['demos'])!={'w01','w02','w03'}:raise ValueError('All three saved viewers are required.')
    for study,detail in config['demos'].items():
        folder=source(detail['folder']);manifest_path=folder/detail['manifest']
        manifest=read(manifest_path)
        if output.is_relative_to(folder):raise ValueError('Output cannot be inside a source bundle.')
        names=set(manifest['files'])|{detail['manifest']}
        if {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}!=names:raise ValueError('Unexpected bundle files.')
        for name,wanted in manifest['files'].items():
            p=(folder/name).resolve()
            if not p.is_relative_to(folder) or sha(p)!=wanted:raise ValueError('Bundle verification failed.')
            copies.append((p,'demos/'+study+'/'+name))
        copies.append((manifest_path,'demos/'+study+'/'+detail['manifest']))
        demo_sources[study]=sha(manifest_path)
    review=read(source(config['video_review']));video=source(config['video'])
    if review['status']!='passed' or sha(video)!=review['video_sha256']:raise ValueError('Video does not match its reviewed recording.')
    copies.append((video,'video/w02-saved-walkthrough.webm'))
    output.mkdir(parents=True)
    for src,name in copies:
        dest=output/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
        if sha(src)!=sha(dest):raise ValueError('Copy verification failed.')
    (output/'index.html').write_text(PAGE,encoding='utf-8')
    manifest={'scope':'Offline presenter package; existing reviewed artifacts and saved executions.',
              'builder_sha256':sha(Path(__file__)),'config_sha256':sha(config_path),
              'presentation_record_sha256':sha(source(config['presentation_record'])),'source_bundle_manifests':demo_sources,
              'new_workload_executions':0,'files':{p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*')) if p.is_file()}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(output.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(output).as_posix())
    print(json.dumps({'status':'built','files':len(manifest['files']),'zip_bytes':archive.stat().st_size,'zip_sha256':sha(archive),'output':str(output)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'presentations/presenter-pack-20261027.json')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build(args.config,args.output)
