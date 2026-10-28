#!/usr/bin/env python3
"""Check the saved W02 viewer in isolated Chrome; optionally record a walkthrough.

Reads a checksum-verified local bundle. No engine, model, credentials or cloud
access is needed. The original bundle is never changed.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_files(bundle):
    manifest = json.loads((bundle / 'bundle-manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        path = (bundle / name).resolve()
        if not path.is_relative_to(bundle) or sha(path) != expected:
            raise ValueError('bundle_integrity_failed')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--playwright-path', type=Path)
    parser.add_argument('--browser-cache', type=Path, help='Optional Playwright encoder/browser cache')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    if output.exists():
        parser.error('Use a fresh output directory; earlier evidence is retained.')
    if output.is_relative_to(bundle):
        parser.error('Output must be outside the sealed bundle.')
    manifest = bundle_files(bundle)
    if args.playwright_path:
        sys.path.insert(0, str(args.playwright_path.resolve()))
    if args.browser_cache:
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(args.browser_cache.resolve())
    from playwright.sync_api import sync_playwright, expect
    output.mkdir(parents=True)
    errors, external, rows = [], [], []
    context = browser = None
    report = {'scope': 'Saved local functional evidence; no new engine execution',
              'bundle_manifest_sha256': sha(bundle/'bundle-manifest.json'),
              'bundle_files_verified': len(manifest['files']),
              'runner_sha256': sha(Path(__file__)),
              'playwright_version': importlib.metadata.version('playwright'),
              'live_model_calls': 0, 'new_payment_executions': 0,
              'status': 'running'}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=not args.headed)
            report['browser_version'] = browser.version
            context = browser.new_context(viewport={'width':1600, 'height':1000},
                                          service_workers='block')
            def block(route):
                external.append(route.request.url.split('?')[0])
                route.abort()
            context.route('http://**/*', block)
            context.route('https://**/*', block)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto((bundle/'index.html').as_uri())
            expect(page.locator('#case option')).to_have_count(10)
            for case in page.locator('#case option').evaluate_all('(xs)=>xs.map(x=>x.value)'):
                page.locator('#case').select_option(case)
                axes = []
                for arm in ('sequential', 'parallel'):
                    original = json.loads((bundle/f'evidence/cases/{arm}-{case}.json').read_text(encoding='utf-8'))
                    expected = original['result']
                    page.get_by_role('button', name=arm.capitalize()+' checks', exact=True).click()
                    expect(page.locator('#'+arm)).to_have_attribute('aria-pressed', 'true')
                    expect(page.locator('#'+arm+'-diagram')).to_be_visible()
                    other = 'parallel' if arm == 'sequential' else 'sequential'
                    expect(page.locator('#'+other+'-diagram')).to_be_hidden()
                    expect(page.locator('#effect-count')).to_have_text(str(expected['effects']))
                    outcome = ('Original payment settled exactly once' if expected['outcome'] == 'settled'
                               else 'No payment: '+expected['outcome'])
                    expect(page.locator('#outcome')).to_have_text(outcome)
                    expect(page.locator('#overlap')).to_have_text(
                        'KYC and risk '+('overlapped' if expected['checks_overlap'] else 'did not overlap'))
                    expect(page.locator('#safety')).to_contain_text(
                        str(expected['replay_calls'])+' repeated calls preserved the result')
                    expect(page.locator('#record')).to_have_attribute('href', f'evidence/cases/{arm}-{case}.json')
                    axes.append(page.locator('#axis').inner_text())
                    rows.append({'case':case, 'arm':arm, 'outcome':expected['outcome'],
                                 'effects':expected['effects'], 'record_sha256':sha(bundle/f'evidence/cases/{arm}-{case}.json')})
                    if case in ('approve','unauthorized','unknown_kyc'):
                        page.screenshot(path=str(output/f'{case}-{arm}.png'), full_page=True)
                assert axes[0] == axes[1], 'timeline_axis_changed_between_arms'
            # Follow a real evidence link and compare the rendered JSON to its file.
            page.locator('#case').select_option('approve')
            page.locator('#parallel').click()
            page.locator('#record').click()
            page.wait_for_url('**/parallel-approve.json')
            shown = json.loads(page.locator('body').inner_text())
            assert shown == json.loads((bundle/'evidence/cases/parallel-approve.json').read_text(encoding='utf-8'))
            page.go_back()
            expect(page.locator('#case')).to_be_visible()
            # Keyboard operation and a common laptop viewport.
            page.set_viewport_size({'width':1366, 'height':768})
            page.locator('#case').focus()
            page.keyboard.press('Home')
            page.keyboard.press('ArrowDown')
            expect(page.locator('#case')).to_have_value('sanctions_reject')
            page.locator('#parallel').focus()
            page.keyboard.press('Enter')
            expect(page.locator('#parallel')).to_have_attribute('aria-pressed','true')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output/'laptop-viewport.png'), full_page=True)
            context.close()
            context = None
            if args.record:
                context = browser.new_context(viewport={'width':1600,'height':1000},
                    record_video_dir=str(output/'video'),
                    record_video_size={'width':1600,'height':1000}, service_workers='block')
                context.route('http://**/*',block)
                context.route('https://**/*',block)
                page = context.new_page()
                page.on('pageerror',lambda error: errors.append(str(error)))
                page.goto((bundle/'index.html').as_uri())
                video = page.video
                # Four explicit stops, 60 seconds total. The viewer labels saved data.
                for case, arm in [('approve','sequential'),('approve','parallel'),
                                  ('unauthorized','parallel'),('unknown_kyc','parallel')]:
                    page.locator('#case').select_option(case)
                    page.locator('#'+arm).click()
                    page.locator('.toolbar').scroll_into_view_if_needed()
                    time.sleep(6)
                    page.locator('.grid').scroll_into_view_if_needed()
                    time.sleep(9)
                context.close()
                context = None
                video.save_as(str(output/'w02-saved-walkthrough.webm'))
                report['video_sha256'] = sha(output/'w02-saved-walkthrough.webm')
            browser.close()
            browser = None
        assert not errors and not external, 'browser_error_or_network_attempt'
        assert len(rows) == 20 and sum(r['effects'] for r in rows) == 8
        bundle_files(bundle)
        report.update(status='passed', checked_cases=rows, keyboard_navigation='passed',
                      evidence_link_navigation='passed', horizontal_overflow=False,
                      javascript_errors=errors, external_network_attempts=external,
                      browser_closed=True, source_bundle_unchanged=True,
                      visual_review='pending; inspect captured screenshots and recording')
    except BaseException as error:
        report.update(status='failed', error_type=type(error).__name__,
                      error=str(error), javascript_errors=errors, external_network_attempts=external)
        raise
    finally:
        # Playwright's context manager stops its own processes on failure.
        (output/'summary.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'checked_cases':len(rows),
                      'recording':args.record,'output':str(output)}))


if __name__ == '__main__':
    main()
