#!/usr/bin/env python3
"""Check the sealed W02 functional report against each original case in Chrome."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.audit_w02_functional import audit,read,sha,verify_inventory


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--playwright-path',type=Path);parser.add_argument('--headed',action='store_true')
    args=parser.parse_args();source=args.source.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(source):parser.error('Use a new review directory outside the sealed source.')
    audited=audit(source);summary=read(source/'summary.json')
    if args.playwright_path:sys.path.insert(0,str(args.playwright_path.resolve()))
    from playwright.sync_api import sync_playwright,expect
    output.mkdir(parents=True);errors=[];network=[]
    report={'status':'running','source_checksums_sha256':sha(source/'checksums.json'),'runner_sha256':sha(Path(__file__)),'new_workload_executions':0}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=not args.headed)
            report['browser_version']=browser.version
            context=browser.new_context(viewport={'width':1600,'height':1000},service_workers='block')
            def block(route):network.append(route.request.url);route.abort()
            context.route('http://**/*',block);context.route('https://**/*',block)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto((source/'report.html').as_uri())
            expect(page.locator('p.scope')).to_have_text('LOCAL FUNCTIONAL VALIDATION - NOT OCI PERFORMANCE')
            rows=page.locator('tr[data-arm]');expect(rows).to_have_count(len(summary['cases']))
            page.screenshot(path=str(output/'report-top.png'))
            for i,record in enumerate(summary['cases']):
                cells=rows.nth(i).locator('td')
                expected=[' '.join(record['case_id'].replace('_',' ').split()),record['arm'],record['status'],record['outcome'],str(record['effects']),'Yes' if record['checks_overlap'] else 'No']
                for j,text in enumerate(expected):expect(cells.nth(j)).to_have_text(text)
                expect(cells.nth(6).locator('a')).to_have_attribute('href',record['arm']+'-'+record['case_id']+'.json')
            for arm in ('sequential','parallel'):
                page.locator('button[data-filter="'+arm+'"]').click()
                expect(page.locator('tr[data-arm]:visible')).to_have_count(audited['unique_scenarios'])
                expect(page.locator('tr[data-arm="'+arm+'"]:visible')).to_have_count(audited['unique_scenarios'])
            page.locator('.results').last.scroll_into_view_if_needed()
            page.screenshot(path=str(output/'parallel-results.png'))
            first=page.locator('tr[data-arm="parallel"]').first
            href=first.locator('a').get_attribute('href');first.locator('a').click();page.wait_for_url('**/'+href)
            assert json.loads(page.locator('body').inner_text())==read(source/href)
            page.go_back();page.set_viewport_size({'width':1366,'height':768})
            page.locator('button[data-filter="sequential"]').focus();page.keyboard.press('Enter')
            expect(page.locator('tr[data-arm="sequential"]:visible')).to_have_count(audited['unique_scenarios'])
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.evaluate('scrollTo(0,0)');page.screenshot(path=str(output/'laptop-top.png'))
            context.close();browser.close()
        assert not errors and not network
        verify_inventory(source)
        report.update(status='passed',case_rows_verified=len(summary['cases']),filters='passed',
                      evidence_navigation='passed',keyboard_navigation='passed',horizontal_overflow=False,
                      javascript_errors=errors,external_network_requests=network,source_unchanged=True,visual_review='pending; inspect screenshots')
    except BaseException as error:
        report.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:
        (output/'summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
