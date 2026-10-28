#!/usr/bin/env python3
"""Automate selection, evidence navigation and screenshots of the saved W03 viewer."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(bundle):
    manifest=json.loads((bundle/'manifest.json').read_text())
    for name,wanted in manifest['files'].items():
        path=(bundle/name).resolve();assert path.is_relative_to(bundle) and sha(path)==wanted
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--playwright-path',type=Path);parser.add_argument('--headed',action='store_true')
    args=parser.parse_args();bundle=args.bundle.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(bundle):parser.error('Use a new output directory outside the sealed bundle.')
    manifest=check(bundle);data=json.loads((bundle/'data.json').read_text())
    if args.playwright_path:sys.path.insert(0,str(args.playwright_path.resolve()))
    from playwright.sync_api import sync_playwright,expect
    output.mkdir(parents=True);errors=[];network=[];checks=[]
    report={'status':'running','new_payment_executions':0,'bundle_files_verified':len(manifest['files']),
        'bundle_manifest_sha256':sha(bundle/'manifest.json'),'runner_sha256':sha(Path(__file__))}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=not args.headed);report['browser_version']=browser.version
            context=browser.new_context(viewport={'width':1600,'height':1000},service_workers='block')
            def block(route):network.append(route.request.url);route.abort()
            context.route('http://**/*',block);context.route('https://**/*',block)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto((bundle/'index.html').as_uri());expect(page.locator('#case option')).to_have_count(10)
            for record in data['records']:
                page.locator('#case').select_option(record['case_id']);page.locator('#'+record['arm']).click()
                expect(page.locator('#'+record['arm'])).to_have_attribute('aria-pressed','true')
                expect(page.locator('#reviews')).to_have_text(str(record['review_tasks']))
                expect(page.locator('#outcome')).to_have_text(record['outcome'].replace('_',' '))
                expect(page.locator('#effects')).to_contain_text(str(record['effects'])+' exact synthetic effect')
                expect(page.locator('#amount')).to_contain_text(str(record['payment']['amount_minor'])+' minor units')
                if record['review_tasks']:
                    expect(page.locator('#review-path')).to_be_visible();expect(page.locator('#direct-path')).to_be_hidden()
                else:
                    expect(page.locator('#review-path')).to_be_hidden();expect(page.locator('#direct-path')).to_be_visible()
                expect(page.locator('#evidence')).to_have_attribute('href',record['evidence'])
                if record['case_id'] in ('eligible_small','over_limit_approve','unknown_checks'):
                    page.screenshot(path=str(output/(record['case_id']+'-'+record['arm']+'.png')),full_page=True)
                checks.append({k:record[k] for k in ('case_id','arm','review_tasks','outcome','effects')})
            page.locator('#case').select_option('eligible_small');page.locator('#policy_routing').click()
            page.locator('#evidence').click();page.wait_for_url('**/policy_routing-eligible_small.raw.json')
            assert json.loads(page.locator('body').inner_text())==json.loads((bundle/'evidence/policy_routing-eligible_small.raw.json').read_text())
            page.go_back();page.set_viewport_size({'width':1366,'height':768})
            page.locator('#case').focus();page.keyboard.press('Home');page.keyboard.press('ArrowDown')
            expect(page.locator('#case')).to_have_value('eligible_at_limit')
            page.locator('#policy_routing').focus();page.keyboard.press('Enter')
            expect(page.locator('#policy_routing')).to_have_attribute('aria-pressed','true')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output/'laptop.png'),full_page=True)
            context.close();browser.close()
        assert not errors and not network and len(checks)==20;check(bundle)
        report.update(status='passed',checks=checks,javascript_errors=errors,external_requests=network,
            keyboard_navigation='passed',evidence_navigation='passed',horizontal_overflow=False,
            original_bundle_unchanged=True,browser_closed=True,visual_review='pending')
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:(output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'case_arm_combinations':len(checks)}))


if __name__=='__main__':main()
