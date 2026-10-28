#!/usr/bin/env python3
"""Check the mixed-outcome saved report against the independently audited data."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.audit_w02_mixed import audit, read, sha, render_report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--playwright-path',type=Path)
    parser.add_argument('--headed',action='store_true');args=parser.parse_args()
    source,report,output=(getattr(args,k).resolve() for k in ('source','report','output'))
    if output.exists() or output.is_relative_to(source) or output.is_relative_to(report):
        parser.error('Use a fresh review directory outside source and report.')
    result=audit(source)
    assert result==read(report/'summary.json'), 'report_data_changed'
    assert (report/'report.html').read_text(encoding='utf-8')==render_report(result), 'rendered_report_changed'
    hashes={n:sha(report/n) for n in ('summary.json','report.html')}
    if args.playwright_path:sys.path.insert(0,str(args.playwright_path.resolve()))
    from playwright.sync_api import sync_playwright,expect
    output.mkdir(parents=True);errors=[];network=[]
    review={'status':'running','report_sha256':hashes,'runner_sha256':sha(Path(__file__)),'new_workload_executions':0}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=not args.headed)
            review['browser_version']=browser.version
            context=browser.new_context(viewport={'width':1600,'height':1000},service_workers='block')
            def block(route):network.append(route.request.url);route.abort()
            context.route('http://**/*',block);context.route('https://**/*',block)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto((report/'report.html').as_uri())
            expect(page.locator('h1')).to_have_text('Mixed payment outcomes under steady load')
            for i,kind in enumerate(('settled','rejected','blocked')):
                expect(page.locator('article strong').nth(i)).to_have_text(str(result['measurement_outcomes'][kind]))
            expect(page.locator('#lanes tbody tr')).to_have_count(4)
            for i,lane in enumerate(result['lanes']):
                m=lane['measurement'];cells=page.locator('#lanes tbody tr').nth(i).locator('th,td')
                expected=[f'{lane["arm"].title()} · {lane["rate"]}/s',str(m['offered'])]
                expected += [f'{m["outcomes"][k]["within_deadline"]} / {m["outcomes"][k]["verified"]}' for k in ('settled','rejected','blocked')]
                expected += [str(m['offered']-m['on_time_correct']),f'{m["unresolved_or_invalid_count"]} / {m["dropped"]}']
                for j,value in enumerate(expected):expect(cells.nth(j)).to_have_text(value)
                detail=page.locator('details').nth(i);detail.locator('summary').click()
                profile_rows=detail.locator('tbody tr');expect(profile_rows).to_have_count(5)
                for j,(profile,data) in enumerate(lane['original_store']['measurement_profiles'].items()):
                    wanted=[profile.replace('_',' '),str(data['offered'])]
                    wanted += [f'{data["within_deadline"].get(k,0)} / {data["outcomes"].get(k,0)}' for k in ('settled','rejected','blocked')]
                    for n,value in enumerate(wanted):expect(profile_rows.nth(j).locator('th,td').nth(n)).to_have_text(value)
                detail.locator('summary').click()
            page.evaluate('scrollTo(0,0)');page.screenshot(path=str(output/'desktop-top.png'))
            first=page.locator('details summary').first;first.focus();page.keyboard.press('Enter')
            expect(page.locator('details').first).to_have_attribute('open','')
            page.locator('details').first.scroll_into_view_if_needed();page.screenshot(path=str(output/'profile-detail.png'))
            page.locator('#evidence').click();page.wait_for_url('**/summary.json')
            assert json.loads(page.locator('body').inner_text())==result
            page.go_back();page.set_viewport_size({'width':1366,'height':768})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.evaluate('scrollTo(0,0)');page.screenshot(path=str(output/'laptop-top.png'))
            context.close();browser.close()
        assert not errors and not network
        assert hashes=={n:sha(report/n) for n in hashes}
        assert audit(source)==result
        review.update(status='passed',lane_rows_verified=4,profile_rows_verified=20,evidence_navigation='passed',
                      keyboard_navigation='passed',horizontal_overflow=False,javascript_errors=errors,
                      external_network_requests=network,source_unchanged=True,visual_review='pending; inspect screenshots')
    except BaseException as error:
        review.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:
        (output/'summary.json').write_text(json.dumps(review,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(review,indent=2))


if __name__=='__main__':main()
