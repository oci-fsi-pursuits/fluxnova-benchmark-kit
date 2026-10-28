"""Build a separate 21 October story source from audited public evidence.

Preserves the earlier story and its layout. Performs no cloud operations.
"""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '8915638'
BASE = f'https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/{COMMIT}/'
FULL = 'oci-20261021-worker-timing-backoff'
RECOVERY = 'oci-20261021-late-poll-recovery'


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


def block(text, y=140, h=65, size=24, bold=False, color='263A38', x=48, w=864):
    return dict(text=text, x=x, y=y, w=w, h=h, size=size, bold=bold, color=color)


def table(headers, rows, widths, heights, y=145, size=18):
    return dict(headers=headers, rows=rows, widths=widths, rowHeights=heights,
                x=48, y=y, fontSize=size)


def slide(title, blocks, notes, lanes, tables=None):
    return dict(title=title, blocks=blocks, tables=tables or [], lines=[], rects=[],
                source='Validated benchmark evidence through 21 October 2026 UTC. Sources and interpretation in speaker notes.',
                notes=notes + '\n\nSources\n' + '\n'.join(BASE + 'validation/' + lane + '/README.md' for lane in lanes))


def main():
    original = load('presentations/fluxnova-story.json')
    assert len(original['slides']) == 15
    result = load(f'validation/{FULL}/summary.json')
    recovery = load(f'validation/{RECOVERY}/summary.json')
    assert result['status'] == recovery['status'] == 'pass_independent_audit'
    assert result['full_runs_completed'] == 10 and result['measured_correct'] == 180000
    assert result['measured_correct_each_setting'] == 90000
    assert result['controls_other_than_timing_equal'] and result['manual_receipt_review_passed']
    assert all(result[k] == 0 for k in ['measured_deadline_misses', 'measured_dropped', 'measured_unknown', 'measured_unsafe_outcomes'])
    assert all(result['cleanup']['original_preservation_checks'].values())
    assert recovery['trials_completed'] == 3 and recovery['measured_correct'] == 1800
    assert not result['causal_speedup_or_equivalence_claimed']
    rows = []
    for pair in range(1, 6):
        runs = [r for r in result['runs'] if r['pair'] == pair]
        on = next(r for r in runs if r['mode'] == 'on')
        off = next(r for r in runs if r['mode'] == 'off')
        delta = result['pairs'][pair-1]['p95_on_minus_off_ms']
        assert abs(on['completion_p95_ms'] - off['completion_p95_ms'] - delta) < 1e-8
        assert on['correct'] == off['correct'] == 18000
        assert on['timing_task_records_for_measured_processes'] == 18000
        assert off['timing_task_records_for_measured_processes'] == 0
        rows.append([str(pair), f"{on['completion_p95_ms']:.2f}", f"{off['completion_p95_ms']:.2f}", f'{delta:+.2f}'])

    slides = copy.deepcopy(original['slides'][:11])
    for b in slides[0]['blocks']:
        b['text'] = b['text'].replace('18 October 2026 UTC', '21 October 2026 UTC')
    slides[0]['notes'] += '\nUpdate: the completed five-pair comparison supplies 180,000 further measured synthetic settlements. These are separate from the two earlier 90,000-workflow reference lanes.'
    slides[7]['notes'] += '\nLater startup diagnostics reproduced another stall with CPU/pool pressure. The initiating cause remains unresolved. The subsequent matched study used identical late-poll safeguards in both modes and passed. Its success does not retrospectively explain these six deadline misses.'

    slides.append(slide('A failed warmup changed the test safeguards',
        [block('The earlier full comparison stopped after one passing pair.', y=132, h=53, size=26, bold=True),
         block('The initiating application delay remains unresolved. Later passes do not erase the failures.', y=431, h=56, size=21, bold=True, color='9A5B23')],
        'The earlier campaign passed one pair, then became invalid on its third run. We kept that series incomplete and retained every failure. A later diagnostic reproduced a warmup stall and the new unknown-outcome guard stopped it at 48.96 seconds before any measurement request. CPU and JDBC-pool pressure are confirmed, while the initiating delay is not. A separate capture defect came from a JSON batch longer than the 64 KB boundary search. The backward scanner now handles long partial batches. The recovery candidate preserves 100 ms polling before the two-second deadline and uses 1,000 ms afterward to limit observer load on already-late work. Three fixed trials recovered after a five-second pause of only the owned worker, with 3,600 correct warmup and 1,800 correct/on-time measured settlements. Those trials used JVM/thread observations and deliberate pauses. They qualify the candidate for another series but are not a controlled capacity or root-cause result.',
        ['oci-20261020-worker-timing-retry', 'oci-20261021-startup-diagnostic-v2', RECOVERY],
        tables=[table(['Safeguard', 'Verified evidence'], [
            ['Stop unknown warmup outcomes', 'Reproduction stopped at 48.96 s before measurement'],
            ['Repair export capture', 'Large partial JSON batches retain verified records'],
            ['Reduce already-late polling', '3/3 recovery trials passed after worker pauses']],
            [310, 554], [34, 65, 65, 65], y=195, size=18)]))

    slides.append(slide('The matched timing study passed all ten runs',
        [block('180,000 / 180,000 correct and on time', y=130, h=55, size=34, bold=True, color='075B51'),
         block('90,000 per setting at 10 starts/s. Zero deadline misses, drops, unknowns or unsafe outcomes.', y=197, h=62, size=23),
         block('Only the worker timing flag changed', y=283, h=38, size=26, bold=True),
         block('Same pinned images and checkpoint, ten-minute warmup, 30-minute measurement, polling and resources.', y=332, h=72, size=24),
         block('Java instrumentation remained on in both modes. The generator shared the isolated controller.', y=439, h=48, size=20, color='576B65')],
        'This prospective series is worker-timing-backoff-v1. It uses five prespecified pairs in order on/off, off/on, on/off, off/on, on/off. Every run restores the same independently rehearsed PostgreSQL/SQLite checkpoint, accepts 6,000 warmup starts and completes 18,000 measured settlements. Warmup totals are 60,000, separate from the 180,000 measured outcomes. Both modes keep 100 ms predeadline polling and 1,000 ms already-late polling, the same warmup-unknown abort, Java instrumentation, two-logical-CPU engine, four GiB container memory, two GiB heap and ten JDBC connections. The generator and isolated stack run on the existing controller. No diagnostic worker pause, JVM dump or thread sampler runs in this study. Absolute latencies cannot be ranked against the earlier separate-host reference lanes. Every run passed correctness, deadline, generator, queue and capture gates. The independent audit reproduces all ten reports and checks archive hashes/CRCs/member checksums, producer timestamp windows and control equality. Sampled trace presence is not complete workflow trace coverage. The prior failed pair does not contribute to this series.',
        [FULL]))

    slides.append(slide('Timing-on and timing-off latency stayed close',
        [block('Completion p95 by pair, milliseconds', y=126, h=35, size=23),
         block('Mean paired difference: -0.40 ms, timing-on minus timing-off', y=423, h=38, size=23, bold=True, color='075B51'),
         block('Five pairs with 100 ms predeadline polling provide limited precision for small differences.', y=464, h=38, size=18, color='576B65')],
        'Each row contains the p95 of one 30-minute run per setting, with 18,000 correct/on-time measured outcomes each. A negative difference means timing-on had the lower observed p95 in that pair. Three pair differences are negative and two are positive. The average paired p95 difference is -0.401069 ms, ranging -1.452316 to +0.514294 ms. The average paired p99 difference is -0.407558 ms. These are differences of per-run percentiles, not a pooled p95 or confidence interval. Completion observations include polling delay. Do not turn the negative mean into a speedup percentage or call the settings equivalent. The evidence shows little observed latency separation at this fixed offered rate, on this shared cell and revised protocol.',
        [FULL], tables=[table(['Pair', 'Timing on p95', 'Timing off p95', 'On minus off'], rows,
            [100, 255, 255, 254], [34, 44, 44, 44, 44, 44], y=167, size=19)]))

    verdict = slide('Worker timing added visibility at the tested load',
        [block('90,000 measured processes with timing records', y=132, h=47, size=29, bold=True, color='075B51'),
         block('Timing-off emitted no task or poll timing records. Both modes passed every measured outcome.', y=192, h=64, size=24),
         block('The result supports keeping diagnostics for this tested workload. It establishes neither zero overhead nor maximum capacity.', y=421, h=72, size=23, bold=True)],
        'Timing-on records cover all 90,000 measured process IDs. No timing attempts contain stage errors. Timing-off emits zero task/poll timing records. The sampled engine CPU mean paired difference is -0.0208196606 percentage points, with pair differences from -0.236939 to +0.345439. These are unnormalized Docker CPU percentages for the engine, not worker overhead, CPU utilization normalized to its two-CPU quota, or total system usage. The mean paired engine memory difference is +4,422,231.87 bytes, or +4.217 MiB, and all five differences are positive. The observed memory direction deserves follow-up if memory sensitivity matters, but this study does not establish a causal memory effect or zero overhead. Per-run memory and CPU means summarize distinct sampled windows and are descriptive. Other costs, worker utilization and long-term log storage are outside these displayed resource summaries. The decision is to keep visibility for this fixed 10/s workload while rechecking other loads. The initial stall remains unresolved.',
        [FULL], tables=[table(['Measured observation', 'Mean paired change, on minus off'], [
            ['Completion p99', '-0.41 ms'], ['Sampled engine CPU', '-0.021 percentage points'],
            ['Sampled engine memory', '+4.22 MiB']], [390, 474], [34, 37, 37, 37], y=265, size=19)])
    slides.append(verdict)

    slides.append(slide('Engineering decisions and remaining work', [],
        'The completed evidence now supports keeping immediate worker fetch after success, retaining 100 ms predeadline completion observations and retaining timing diagnostics at the tested 10/s load. Slower already-late observation is a separate explicitly declared safeguard. Repeated async and settlement lanes each establish their own fixed-rate references. The matched study supplies a scoped comparison of worker timing, not live-model payment throughput or a broad optimization claim. Next functional work is signing-key rotation and unplanned failover, each with isolated state, exact ownership and fresh cost coverage. Real identity/payment integration, human usability and maximum-capacity testing remain open.',
        ['oci-20261013-worker-pacing', 'oci-20261013-polling', FULL, 'oci-20261018-primary-reboot'],
        tables=[table(['Evidence', 'Decision or next validation'], [
            ['Worker pacing and polling', 'Keep immediate fetch and 100 ms predeadline polling'],
            ['Matched timing at 10/s', 'Keep diagnostic visibility within the tested scope'],
            ['Startup and model tails', 'Keep failures open and collect targeted evidence'],
            ['Payment trust and recovery', 'Test key rotation and unplanned failover next'],
            ['Production sizing and cost', 'Measure capacity and reconcile complete billing']],
            [310, 554], [38, 58, 58, 58, 58, 58], y=145, size=19)]))

    slides.append(copy.deepcopy(original['slides'][13]))
    slides[-1]['notes'] += '\nThis earlier Java-agent pilot is distinct from the 21 October worker timing study. Java instrumentation stays attached in both modes of that later study.'
    allocation = copy.deepcopy(original['slides'][14])
    allocation['blocks'] = [
        block('Persistent reference cell', y=136, h=35, size=25, bold=True),
        block('Four Compute VMs and one managed PostgreSQL node\n10 allocated OCPUs and 80 GB memory in Phoenix', y=183, h=75, size=23),
        block('Comparison cleanup and planning allowance', y=296, h=35, size=25, bold=True, color='075B51'),
        block('Temporary two-node study database deleted. Source cell preserved.\n$167.73 reserved within $175 through 21 October 19:00 UTC.', y=343, h=75, size=23),
        block('Planning reserves are not actual billing or dollars per payment. The original cell remains allocated.', y=443, h=46, size=19, color='576B65')]
    allocation['notes'] = 'The persistent reference allocation is unchanged: four fixed Compute VMs and one managed PostgreSQL node, ten OCPUs and eighty GB total. No autoscaling is configured. The completed comparison used a temporary two-node managed system and isolated containers on the existing controller, not new Compute VMs. The measured engine limit was two logical CPUs, four GiB memory and a two GiB heap. Test containers, network and temporary database are removed. Original access, images/restarts, verifier PID, schema, non-meter data and all older meter rows match, allowing routine meter append. Final queues are empty. Test and audit access are closed and every temporary sleep request is released. The original cell remains allocated and billable. The historical whole-cell reservation of $167.728 covers through 21 October 19:00 UTC under the approved $175 allowance. Exact-resource posted usage was $40.99 at preflight and incomplete. This slide does not refresh actual billing, change the deployed $100 alert or apply Terraform. Complete cost attribution, maximum capacity, real payment/identity integration, signing-key rotation and unplanned failover remain open.\n\nSources\n' + BASE + 'validation/' + FULL + '/README.md'
    slides.append(allocation)
    assert len(slides) == 18
    # Historical slides retain their original dated citations. New claims point
    # to the already committed public evidence, not to an unpublished worktree.
    out = dict(title=original['title'], source_commit=COMMIT, as_of_utc='2026-10-21',
               prior_story='presentations/fluxnova-story.json',
               font_policy=dict(basis='reference', families=['Arial']),
               slides=slides)
    inputs = ['presentations/fluxnova-story.json'] + [f'validation/{lane}/summary.json' for lane in [FULL, RECOVERY]]
    out['input_sha256'] = {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in inputs}
    path = ROOT / 'presentations/fluxnova-story-20261021.json'
    path.write_text(json.dumps(out, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(slides=len(slides), native_tables=sum(len(s['tables']) for s in slides),
                          evidence_status=result['status'], output=str(path), cloud_operations=False)))


if __name__ == '__main__':
    main()
