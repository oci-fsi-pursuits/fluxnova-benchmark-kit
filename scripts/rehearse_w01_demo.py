#!/usr/bin/env python3
"""Verify all sixty saved W01 burst selections and their plotted observations."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(bundle):
    manifest=json.loads((bundle/'manifest.json').read_text())
    for name,wanted in manifest['files'].items():
        p=(bundle/name).resolve();assert p.is_relative_to(bundle) and sha(p)==wanted
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--playwright-path',type=Path);parser.add_argument('--headed',action='store_true')
    args=parser.parse_args();bundle=args.bundle.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(bundle):parser.error('Use a fresh output directory outside the sealed bundle.')
    manifest=check(bundle);data=json.loads((bundle/'data.json').read_text());summary=data['summary']
    assert summary['status']=='complete' and summary['completed_arms']==10
    if args.playwright_path:sys.path.insert(0,str(args.playwright_path.resolve()))
    from playwright.sync_api import sync_playwright,expect
    output.mkdir(parents=True);errors=[];network=[];count=0;checked_payments=0
    report={'status':'running','new_payment_executions':0,'bundle_files_verified':len(manifest['files']),
        'bundle_manifest_sha256':sha(bundle/'manifest.json'),'runner_sha256':sha(Path(__file__))}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=not args.headed);report['browser_version']=browser.version
            context=browser.new_context(viewport={'width':1600,'height':1100},service_workers='block')
            def block(route):network.append(route.request.url);route.abort()
            context.route('http://**/*',block);context.route('https://**/*',block)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto((bundle/'index.html').as_uri())
            expect(page.locator('#pair option')).to_have_count(5);expect(page.locator('#burst option')).to_have_count(12)
            expect(page.locator('#correct')).to_have_text(str(summary['measured_correct'])+' / '+str(summary['measured_offered']))
            for i,pair in enumerate(data['pairs'],1):
                page.locator('#pair').select_option(str(i))
                for burst in range(12):
                    page.locator('#burst').select_option(str(burst))
                    for prefix,arm in [('control','success_delay_100ms'),('candidate','idle_only')]:
                        expected=pair[arm]['bursts'][burst]
                        actual=page.locator('#'+prefix+'-chart .mark').evaluate_all('(xs)=>xs.map(x=>({start:Number(x.getAttribute("x1")),end:Number(x.getAttribute("x2")),onTime:x.classList.contains("on-time")}))')
                        assert len(actual)==20
                        for shown,row in zip(actual,expected):
                            assert abs(shown['start']-(52+row['planned_s']/data['axis_max_s']*560))<1e-6
                            assert abs(shown['end']-(52+row['observed_s']/data['axis_max_s']*560))<1e-6
                            assert shown['onTime']==row['on_time']
                        n=sum(r['on_time'] for r in expected)
                        expect(page.locator('#'+prefix+'-burst-count')).to_have_text(f'{n} / 20 on time · {20-n} late')
                        expect(page.locator('#'+prefix+'-evidence')).to_have_attribute('href',pair[arm]['evidence'])
                        checked_payments+=len(actual)
                    if i in (1,5) and burst in (0,11):page.screenshot(path=str(output/f'pair-{i}-burst-{burst+1}.png'),full_page=True)
                    count+=1
            page.get_by_role('link',name='Independent audit and pair results').click();page.wait_for_url('**/summary.json')
            assert json.loads(page.locator('body').inner_text())==summary;page.go_back()
            page.set_viewport_size({'width':1366,'height':768});page.locator('#pair').focus()
            page.keyboard.press('Home');page.keyboard.press('ArrowDown');expect(page.locator('#pair')).to_have_value('2')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output/'laptop.png'),full_page=True)
            context.close();browser.close()
        assert count==60 and checked_payments==2400 and not errors and not network;check(bundle)
        report.update(status='passed',paired_burst_selections=count,plotted_original_payments_checked=checked_payments,
            javascript_errors=errors,external_requests=network,keyboard_navigation='passed',evidence_navigation='passed',
            horizontal_overflow=False,original_bundle_unchanged=True,browser_closed=True,visual_review='pending')
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:(output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'paired_burst_selections':count,'plotted_original_payments_checked':checked_payments}))


if __name__=='__main__':main()
