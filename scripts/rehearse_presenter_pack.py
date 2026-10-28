#!/usr/bin/env python3
"""Verify offline package links, playback and keyboard use in an isolated browser."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def verify(folder):
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    for name,wanted in manifest['files'].items():
        p=(folder/name).resolve()
        assert p.is_relative_to(folder) and sha(p)==wanted
    assert {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}==set(manifest['files'])|{'manifest.json'}
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--playwright-path',type=Path);parser.add_argument('--headed',action='store_true')
    args=parser.parse_args();bundle=args.bundle.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(bundle):parser.error('Use a new review directory outside the bundle.')
    manifest=verify(bundle)
    if args.playwright_path:sys.path.insert(0,str(args.playwright_path.resolve()))
    from playwright.sync_api import sync_playwright,expect
    output.mkdir(parents=True);errors=[];network=[];children=[]
    report={'status':'running','bundle_manifest_sha256':sha(bundle/'manifest.json'),'runner_sha256':sha(Path(__file__)),'new_workload_executions':0}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=not args.headed)
            report['browser_version']=browser.version
            context=browser.new_context(viewport={'width':1600,'height':1000},service_workers='block')
            def block(route):network.append(route.request.url);route.abort()
            context.route('http://**/*',block);context.route('https://**/*',block)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto((bundle/'index.html').as_uri())
            expect(page.locator('h1')).to_contain_text('Benchmarking Fluxnova')
            for href in ['slides/talk.pptx','slides/summary.pptx','slides/team-brief.pdf']:
                expect(page.locator('a[href="'+href+'"]').first).to_be_visible()
                assert (bundle/href).is_file()
            page.wait_for_function('document.querySelector("video").readyState >= 1')
            duration=page.locator('video').evaluate('(v)=>v.duration')
            assert 60<=duration<=90
            page.screenshot(path=str(output/'index-desktop.png'),full_page=True)
            for selector,study in [('#main-demo','w02'),('#w01-demo','w01'),('#w03-demo','w03')]:
                page.locator(selector).click();page.wait_for_url('**/demos/'+study+'/index.html')
                expect(page.locator('h1')).to_be_visible()
                children.append({'study':study,'title':page.title(),'heading':page.locator('h1').inner_text()})
                page.go_back();expect(page.locator('#main-demo')).to_be_visible()
            page.locator('video').evaluate('async v=>{v.muted=true;await v.play()}')
            page.wait_for_function('document.querySelector("video").currentTime > 0.5')
            page.locator('video').evaluate('v=>v.pause()')
            page.locator('video').screenshot(path=str(output/'recording-playback.png'))
            page.set_viewport_size({'width':1366,'height':768})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#main-demo').focus();page.keyboard.press('Enter')
            page.wait_for_url('**/demos/w02/index.html');page.go_back()
            page.screenshot(path=str(output/'index-laptop.png'),full_page=True)
            context.close();browser.close()
        assert not errors and not network
        assert verify(bundle)==manifest
        report.update(status='passed',files_verified=len(manifest['files']),demo_navigation=children,
                      video_duration_s=duration,video_playback='passed',keyboard_navigation='passed',
                      horizontal_overflow=False,javascript_errors=errors,external_network_requests=network,
                      source_unchanged=True,visual_review='pending; inspect screenshots',venue_display_check='pending')
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:
        (output/'summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
