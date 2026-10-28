#!/usr/bin/env python3
"""Build and optionally browser-check a saved, independently audited M2 report."""
import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import runpy
import sys

KINDS=('settled','rejected','blocked')
LABELS={'settled':'Settlements','rejected':'Expected refusals','blocked':'Safe stops'}


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def fmt(value):return '—' if value is None else f'{value:,.1f}'


def verify_audit(evidence,summary):
    """Re-run the retained auditor against its original sealed inputs."""
    seal=read(evidence/'export-checksums.json')
    for name,digest in seal['files'].items():
        path=(evidence/name).resolve()
        assert path.is_relative_to(evidence) and path.is_file() and sha(path)==digest,'sealed_input_changed'
    actual={p.relative_to(evidence).as_posix() for p in evidence.rglob('*') if p.is_file() and p!=evidence/'export-checksums.json'}
    assert actual==set(seal['files']),'sealed_inventory_changed'
    auditor=evidence/'source/scripts/audit_w02_mixed_cloud.py'
    result=runpy.run_path(str(auditor))['audit'](evidence,summary['phase'])
    assert result==summary,'independent_audit_changed'
    return result


def render_report(result):
    """Use outcome-specific denominators and never summarize an incomplete series."""
    complete=result['phase']=='campaign' and result['status']=='complete' and result['completed_arms']==10
    comparison=result.get('comparison') if complete else None
    if complete and not comparison:raise ValueError('Complete campaign has no audited comparison')
    if comparison and len(comparison['paired_runs'])!=5:raise ValueError('Five pairs are required')
    cards=''.join(f'<article><span>{LABELS[k]}</span><strong>{result["measurement_outcomes"][k]:,}</strong><small>verified measured outcomes</small></article>' for k in KINDS)
    phase='Five-pair comparison' if comparison else 'Qualification only' if result['phase']=='qualification' else 'Incomplete comparison'
    headline='No aggregate improvement claim from this phase.'
    chart='';outcomes='';pairs=''
    if comparison:
        delta=comparison['median_paired_on_time_settlement_change_per_s'];bounds=comparison['paired_on_time_settlement_change_per_s_range']
        headline=f'{delta:+.4f} timely settlements/s: median paired change'
        extent=max(.001,max(abs(p['parallel_on_time_settlement_change_per_s']) for p in comparison['paired_runs']))
        chart='<svg viewBox="0 0 800 240" role="img" aria-label="Parallel minus sequential timely settlements per second, by pair">'
        chart+='<line x1="400" y1="15" x2="400" y2="210" stroke="#526970"/><text x="400" y="233" text-anchor="middle">0</text>'
        for i,pair in enumerate(comparison['paired_runs']):
            delta_i=pair['parallel_on_time_settlement_change_per_s'];width=abs(delta_i)/extent*240;y=25+i*37
            chart+=f'<text x="20" y="{y+16}">Pair {pair["pair"]}</text><rect x="{400 if delta_i>=0 else 400-width}" y="{y}" width="{width}" height="23" fill="{"#087e67" if delta_i>=0 else "#ba4d30"}"/><text x="685" y="{y+16}">{delta_i:+.4f}/s</text>'
        chart+='</svg>'
        chart+=f'<p>Pair range {bounds[0]:+.4f} to {bounds[1]:+.4f}/s. Positive means more timely settlements in parallel; the range is not a confidence interval.</p>'
        for arm in ('sequential','parallel'):
            for kind in KINDS:
                r=comparison['arms'][arm]['outcomes'][kind]
                outcomes+=f'<tr><th>{arm.title()}</th><td>{LABELS[kind]}</td><td>{r["verified"]} / {r["expected"]}</td><td>{r["within_deadline"]} / {r["expected"]}</td><td>{r["deadline_misses"]}</td><td>{fmt(r["median_of_run_p95_ms"])}</td><td>{fmt(r["median_of_run_p99_ms"])}</td></tr>'
        outcomes='<h2>What changed for each outcome?</h2><div class="table-scroll"><table id="outcomes"><caption>All measured runs. Percentiles are medians of run percentiles, not pooled request percentiles.</caption><thead><tr><th>Workflow</th><th>Outcome</th><th>Verified / expected</th><th>Within 2 s / expected</th><th>Late</th><th>p95, ms</th><th>p99, ms</th></tr></thead><tbody>'+outcomes+'</tbody></table></div>'
        for pair in comparison['paired_runs']:
            for kind in KINDS:
                row=pair['outcomes'][kind];change=row['parallel_on_time_fraction_change_percentage_points']
                pairs+=f'<tr class="{"regression" if change<0 else ""}"><th>{pair["pair"]}</th><td>{LABELS[kind]}</td><td>{row["sequential_within_deadline"]}</td><td>{row["parallel_within_deadline"]}</td><td>{change:+.1f} pp</td><td>{fmt(row["sequential_p95_ms"])}</td><td>{fmt(row["parallel_p95_ms"])}</td></tr>'
        pairs='<details id="pairs"><summary>Inspect all five pairs, including regressions</summary><div class="table-scroll"><table><caption>Negative on-time changes are highlighted. Each outcome keeps its own denominator.</caption><thead><tr><th>Pair</th><th>Outcome</th><th>Sequential ≤2 s</th><th>Parallel ≤2 s</th><th>On-time change</th><th>Sequential p95, ms</th><th>Parallel p95, ms</th></tr></thead><tbody>'+pairs+'</tbody></table></div></details>'
    profiles=''
    for store in result['persisted_store_audit']:
        rows=''
        for profile,row in store['measurement_profiles'].items():
            values=''.join(f'<td>{row["within_deadline"].get(k,0)} / {row["outcomes"].get(k,0)}</td>' for k in KINDS)
            rows+=f'<tr><th>{escape(profile.replace("_"," "))}</th><td>{row["offered"]}</td>{values}</tr>'
        profiles+=f'<details class="profile"><summary>{escape(store["folder"])}</summary><div class="table-scroll"><table><caption>Within two seconds / verified outcomes, by dependency profile</caption><thead><tr><th>Profile</th><th>Offered</th><th>Settlements</th><th>Refusals</th><th>Safe stops</th></tr></thead><tbody>{rows}</tbody></table></div></details>'
    rate=result.get('rate_per_s',result.get('selected_rate_per_s'))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Fluxnova: mixed payment outcomes on OCI</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#edf3f2;color:#193c37;font:18px/1.5 system-ui,sans-serif}}main{{max-width:1240px;margin:auto;padding:40px 28px 70px}}header{{background:#173f39;color:#fff;border-radius:18px;padding:32px}}h1{{font-size:38px;line-height:1.15;margin:10px 0 20px}}h2{{font-size:25px;margin:30px 0 12px}}p{{max-width:100ch}}.eyebrow{{font-size:14px;text-transform:uppercase;letter-spacing:.1em;color:#c8eee3}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:22px 0}}article{{background:white;border:1px solid #cad8d4;padding:20px;border-radius:12px}}article strong{{display:block;font-size:38px}}article small{{display:block;color:#526970}}.finding{{background:#fff;padding:22px;border-radius:12px;border-left:6px solid #16846c}}.finding h2{{margin-top:0}}svg{{max-width:850px;width:100%;font:17px system-ui}}table{{width:100%;border-collapse:collapse;background:white;font-variant-numeric:tabular-nums}}th,td{{padding:11px;text-align:left;border-bottom:1px solid #d3dedb}}thead{{background:#234c44;color:white}}caption{{text-align:left;font-size:15px;color:#526970;padding:12px 0}}.table-scroll{{overflow-x:auto}}details{{margin:12px 0;border:1px solid #b8ccc5;border-radius:8px;background:#fff;padding:14px}}summary{{cursor:pointer;font-weight:700}}.regression{{background:#fff0e9}}.note{{border-left:4px solid #ba4d30;padding:8px 16px}}a{{color:#006b59}}a:focus,summary:focus{{outline:3px solid #d7783f;outline-offset:4px}}footer{{margin-top:32px;border-top:1px solid #becfc9;padding-top:20px;font-size:14px;color:#526970;overflow-wrap:anywhere}}@media(max-width:700px){{main{{padding:18px 12px}}header{{padding:22px}}h1{{font-size:29px}}.cards{{grid-template-columns:1fr}}}}
</style></head><body><main><header><div class="eyebrow">Fluxnova research · W02-M2 · saved audited evidence</div><h1>Do parallel checks deliver more timely, correct payments?</h1><p>Sequential and parallel independent checks face the same synthetic mix: 40% settlements, 40% refusals and 20% safe stops. Both retain two workers and the same authorization rules.</p><strong id="phase">{phase}</strong> · Qualified rate: {rate}/s</header><section class="cards" aria-label="Verified measured outcomes">{cards}</section><section class="finding"><h2 id="finding">{headline}</h2>{chart}<p>Only correct settlements observed within two seconds count toward settlement goodput. Refusals and safe stops are reported separately.</p></section>{outcomes}{pairs}<h2>Inspect dependency profiles</h2><p>Each run retains fast, balanced, KYC-dominant, risk-dominant and both-slow cases, including one-time faults. Counts below come from the original store audit.</p>{profiles}<p class="note">Synthetic coverage, not a representative customer mix. No live model calls. Observed completion includes 100 ms polling; this is not maximum capacity or production certification. Warmup outcomes are excluded from the cards and measured comparisons.</p><p><a id="evidence" href="summary.json">Open the complete audited result (JSON)</a> · <a href="manifest.json">Report provenance</a></p><footer>Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.<br>Executed source: {escape(result['source_commit'])}<br>Verified exported files: {result['export_files_verified']:,}. Saved evidence only; opening this page submits no payments.</footer></main></body></html>'''


def browser_check(result,output,review,playwright_path=None):
    if playwright_path:sys.path.insert(0,str(playwright_path.resolve()))
    from playwright.sync_api import sync_playwright,expect
    review.mkdir();errors=[];network=[];rows_verified=0;pair_rows=0;profile_rows=0
    record={'status':'running','new_workload_executions':0,'visual_review':'pending'}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            context=browser.new_context(viewport={'width':1600,'height':1000},service_workers='block')
            def block(route):network.append(route.request.url);route.abort()
            context.route('http://**/*',block);context.route('https://**/*',block)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto((output/'report.html').as_uri())
            expect(page.locator('h1')).to_have_text('Do parallel checks deliver more timely, correct payments?')
            for i,k in enumerate(KINDS):expect(page.locator('article strong').nth(i)).to_have_text(f'{result["measurement_outcomes"][k]:,}')
            comparison=result.get('comparison') if result['phase']=='campaign' and result['status']=='complete' and result['completed_arms']==10 else None
            if comparison:
                expect(page.locator('#phase')).to_have_text('Five-pair comparison')
                expect(page.locator('#finding')).to_have_text(f'{comparison["median_paired_on_time_settlement_change_per_s"]:+.4f} timely settlements/s: median paired change')
                expect(page.locator('#outcomes tbody tr')).to_have_count(6)
                for arm in ('sequential','parallel'):
                    for k in KINDS:
                        r=comparison['arms'][arm]['outcomes'][k]
                        wanted=[arm.title(),LABELS[k],f'{r["verified"]} / {r["expected"]}',f'{r["within_deadline"]} / {r["expected"]}',str(r['deadline_misses']),fmt(r['median_of_run_p95_ms']),fmt(r['median_of_run_p99_ms'])]
                        expect(page.locator('#outcomes tbody tr').nth(rows_verified).locator('th,td')).to_have_text(wanted);rows_verified+=1
                page.locator('#pairs summary').click();expect(page.locator('#pairs tbody tr')).to_have_count(15)
                for pair in comparison['paired_runs']:
                    for k in KINDS:
                        r=pair['outcomes'][k];change=r['parallel_on_time_fraction_change_percentage_points']
                        wanted=[str(pair['pair']),LABELS[k],str(r['sequential_within_deadline']),str(r['parallel_within_deadline']),f'{change:+.1f} pp',fmt(r['sequential_p95_ms']),fmt(r['parallel_p95_ms'])]
                        row=page.locator('#pairs tbody tr').nth(pair_rows);expect(row.locator('th,td')).to_have_text(wanted)
                        if change<0:expect(row).to_have_class('regression')
                        pair_rows+=1
                page.locator('#pairs').scroll_into_view_if_needed();page.screenshot(path=str(review/'paired-details.png'));page.locator('#pairs summary').click()
            else:
                expect(page.locator('#finding')).to_have_text('No aggregate improvement claim from this phase.')
                expect(page.locator('#outcomes')).to_have_count(0)
            expect(page.locator('details.profile')).to_have_count(len(result['persisted_store_audit']))
            for i,store in enumerate(result['persisted_store_audit']):
                detail=page.locator('details.profile').nth(i);detail.locator('summary').click()
                expect(detail.locator('tbody tr')).to_have_count(5)
                for j,(profile,row) in enumerate(store['measurement_profiles'].items()):
                    wanted=[profile.replace('_',' '),str(row['offered'])]+[f'{row["within_deadline"].get(k,0)} / {row["outcomes"].get(k,0)}' for k in KINDS]
                    expect(detail.locator('tbody tr').nth(j).locator('th,td')).to_have_text(wanted);profile_rows+=1
                detail.locator('summary').click()
            page.evaluate('scrollTo(0,0)');page.screenshot(path=str(review/'desktop-top.png'))
            keyboard_checked=page.locator('details').count()>0
            if keyboard_checked:
                first=page.locator('details').first;first.locator('summary').focus();page.keyboard.press('Enter')
                expect(first).to_have_attribute('open','');first.scroll_into_view_if_needed();page.screenshot(path=str(review/'profile-detail.png'))
            page.locator('#evidence').click();page.wait_for_url('**/summary.json');assert json.loads(page.locator('body').inner_text())==result
            page.go_back();page.set_viewport_size({'width':1366,'height':768});page.evaluate('scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(review/'laptop-top.png'))
            record['browser_version']=browser.version;context.close();browser.close()
        assert not errors and not network
        record.update(status='passed',outcome_rows_verified=rows_verified,pair_rows_verified=pair_rows,profile_rows_verified=profile_rows,
            evidence_navigation='passed',keyboard_navigation='passed' if keyboard_checked else 'not_applicable',horizontal_overflow=False,javascript_errors=errors,external_network_requests=network)
    except BaseException as error:
        record.update(status='failed',error_type=type(error).__name__,error=str(error));raise
    finally:(review/'summary.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--audit',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--browser-review',type=Path)
    parser.add_argument('--playwright-path',type=Path);args=parser.parse_args()
    evidence,audited,output=(getattr(args,k).resolve() for k in ('evidence','audit','output'))
    review=args.browser_review.resolve() if args.browser_review else None
    if output.exists() or output.is_relative_to(evidence) or output.is_relative_to(audited):parser.error('Use a fresh output directory outside retained evidence and audit.')
    if review and (review.exists() or any(review.is_relative_to(p) or p.is_relative_to(review) for p in (evidence,audited,output))):parser.error('Use a separate fresh browser-review directory.')
    result=verify_audit(evidence,read(audited/'summary.json'));output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/'report.html').write_text(render_report(result),encoding='utf-8')
    manifest={'source_audit_sha256':sha(audited/'summary.json'),'export_checksums_sha256':sha(evidence/'export-checksums.json'),
        'renderer_sha256':sha(Path(__file__)),'files':{n:sha(output/n) for n in ('summary.json','report.html')},'new_workload_executions':0}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    review_result=browser_check(result,output,review,args.playwright_path) if review else None
    assert manifest['files']=={n:sha(output/n) for n in manifest['files']},'report_changed_during_review'
    verify_audit(evidence,result)
    print(json.dumps({'status':'built','phase':result['phase'],'paired_claim':result['phase']=='campaign' and result['status']=='complete',
        'output':str(output),'browser_review':review_result}))


if __name__=='__main__':main()
