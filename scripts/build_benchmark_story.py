"""Reconcile published observations and build a sourced, editable slide narrative.

This is offline analysis of existing evidence, not a new cloud benchmark.
Run from the repository root. The earlier presentation source stays unchanged.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '4b84645'
BASE = f'https://github.com/oci-fsi-pursuits/fluxnova-benchmarking/blob/{COMMIT}/'
sources = {}


def evidence(lane):
    path = ROOT / 'validation' / lane / 'summary.json'
    sources[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return json.loads(path.read_text(encoding='utf-8'))


def block(text, x=48, y=140, w=864, h=70, size=24, bold=False, color='263A38'):
    return dict(text=text, x=x, y=y, w=w, h=h, size=size, bold=bold, color=color)


def rule(y, x1=48, x2=912):
    return dict(x1=x1, y1=y, x2=x2, y2=y, color='CAD6D0', weight=1)


def table(headers, rows, widths, heights, y=145, size=18):
    return dict(headers=headers, rows=rows, widths=widths, rowHeights=heights,
                x=48, y=y, fontSize=size)


slides = []


def slide(title, blocks, notes, lanes, tables=None, lines=None, rects=None, source=None):
    urls = [BASE + 'validation/' + lane + '/README.md' for lane in lanes]
    slides.append(dict(title=title, blocks=blocks, tables=tables or [],
                       lines=lines or [], rects=rects or [],
                       source=source or 'Fluxnova benchmark reports, 13-18 October 2026 UTC. Details and limitations in speaker notes.',
                       notes=notes + '\n\nSources\n' + '\n'.join(urls)))


def main():
    discovery = evidence('oci-20261013-discovery')
    worker = evidence('oci-20261013-worker-pacing')
    polling = evidence('oci-20261013-polling')
    baseline = evidence('oci-20261014-baseline')
    settlement = evidence('oci-20261015-settlement-batch')
    pilot = evidence('oci-20261015-settlement')
    repeat = evidence('oci-20261015-settlement-repeat')
    overhead = evidence('oci-20261014-overhead')
    evidence('oci-20261016-live-model-trials')
    promotion = evidence('oci-20261018-engine-reconnect')
    reboot = evidence('oci-20261018-primary-reboot')

    groups = {r['poll_interval_ms']: r for r in polling['by_poll_interval']}
    assert groups[100]['started'] == groups[500]['started'] == 2400
    engine_reduction = 100 * (1 - groups[500]['engine_requests'] / groups[100]['engine_requests'])
    history_reduction = 100 * (1 - groups[500]['history_polls'] / groups[100]['history_polls'])
    selected = [r for r in polling['runs'] if r.get('rate') == 10]
    assert [r['poll_interval_ms'] for r in selected] == [100, 500, 500, 100]
    assert all(r['status'] == 'pass' and r['counts']['correct'] == 1200 for r in selected)
    for interval in [100, 500]:
        rs = [r for r in selected if r['poll_interval_ms'] == interval]
        assert sum(r['request_counters']['measurement_engine_requests'] for r in rs) == groups[interval]['engine_requests']
        assert sum(r['request_counters']['measurement_history_polls'] for r in rs) == groups[interval]['history_polls']
    assert baseline['recorded_controls_and_source_hashes_equal']
    assert baseline['correct_measured_workflows'] == settlement['correct_measured_workflows'] == 90000
    assert baseline['dataset_sha256'] != settlement['dataset_sha256']
    assert repeat['same_images_runtime_workload_and_source_as_failed_10ps']
    failed = next(r for r in pilot['runs'] if r['rate'] == 10)
    assert failed['counts']['deadline_misses'] == 6 and failed['counts']['correct'] == 3000
    assert repeat['runs'][0]['counts']['deadline_misses'] == 0 and not repeat['root_cause_established']
    assert next(r for r in worker['runs'] if r['rate'] == 10)['counts']['deadline_correct'] == 3000
    assert next(r for r in worker['runs'] if r['rate'] == 20)['status'] == 'invalid'
    assert reboot['workload']['unique_completed_histories'] == reboot['workload']['expected_payment_effects'] == 122

    def ranges(d):
        return {q: [round(min(d['run_' + q + '_seconds']) * 1000, 1),
                    round(max(d['run_' + q + '_seconds']) * 1000, 1)] for q in ['p95', 'p99']}

    assert ranges(baseline) == {'p95': [245.7, 249.0], 'p99': [247.6, 256.1]}
    assert ranges(settlement) == {'p95': [246.1, 248.3], 'p99': [248.9, 255.9]}
    audit = {
        'status': 'pass', 'scope': 'Offline reconciliation of published observations. No new cloud load or configuration change.',
        'source_commit': COMMIT, 'source_sha256': sources,
        'polling': {'order_ms': [100, 500, 500, 100], 'workflows_per_setting': 2400,
                    'engine_requests_100ms': groups[100]['engine_requests'], 'engine_requests_500ms': groups[500]['engine_requests'],
                    'engine_request_reduction_percent': engine_reduction, 'history_request_reduction_percent': history_reduction,
                    'classification': 'Exploratory observed traffic/detection-delay tradeoff; no state reset or capacity inference'},
        'repeated_baselines': {'latency_unit': 'ms', 'async': ranges(baseline), 'settlement': ranges(settlement),
                               'correct_per_lane': 90000, 'matched_ab_comparison': False,
                               'different_checkpoint': True, 'other_differences': ['warmup', 'workload', 'worker diagnostics/image'],
                               'causal_speedup_claim_supported': False},
        'settlement_deadlines': {'initial_misses': 6, 'initial_attempts': 3000, 'unchanged_repeat_misses': 0,
                                  'unchanged_repeat_attempts': 3000, 'root_cause_known': False, 'fix_demonstrated': False},
        'worker_pacing': {'change': 'Remove 100 ms sleep after successful tasks, retain idle/error backoff',
                          'later_10ps_deadline_correct': 3000, 'later_20ps_status': 'invalid',
                          'controlled_speedup_ratio_supported': False},
        'live_systems_mutated': False, 'new_native_requests': 0,
        'next_experiment': {'status': 'prespecified draft, not launched', 'question': 'Does worker timing materially change settlement latency or resource use?',
                            'candidate_control': 'runtime.worker_timing_enabled only, same pinned worker image',
                            'design': 'Five paired 30-minute runs per setting, balanced order fixed before launch',
                            'pair_order': [['on', 'off'], ['off', 'on'], ['on', 'off'], ['off', 'on'], ['on', 'off']],
                            'order_note': 'Five pairs give three on-first and two off-first pairs. Do not select order from observed results.',
                            'per_run': {'rate_per_s': 10, 'warmup_s': 600, 'measurement_s': 1800, 'drain_s': 30, 'deadline_s': 2, 'poll_interval_ms': 100},
                            'fixed_controls': ['same isolated database/ledger checkpoint and workload', '10 starts/s', 'same 600 s warmup',
                                               '100 ms polling', 'two-second deadline', 'same JVM/agent/resources', 'same idle background services'],
                            'launch_gates': ['fresh cost window for complete run and cleanup', 'isolated owned stack, no original-state reset',
                                             'checkpoint compatibility and 25-case golden pass in both modes', 'generator/telemetry validity', 'queue drain and recovery plan'],
                            'report': 'Per-run percentiles and paired differences, all failures/unknowns, request counts and resource use. No pooled percentile from per-run percentiles.'}}
    dest = ROOT / 'validation/local-20261018-comparison-audit'
    dest.mkdir(exist_ok=True)
    (dest / 'summary.json').write_text(json.dumps(audit, indent=2) + '\n', encoding='utf-8')

    slide('', [block('Benchmarking agentic payments\non Fluxnova', y=95, h=130, size=44, bold=True),
               block('Why we measured, what changed,\nand what the results support', y=250, h=90, size=29),
               block('OCI technical proof of concept\nEvidence through 18 October 2026 UTC', y=405, h=57, size=18, color='576B65')],
          'Opening: We are evaluating whether a workflow platform can support synthetic agentic payments with observable performance and controlled business effects. The goal is a repeatable engineering reference. The results do not compare vendors or establish production capacity. This revision connects engineering changes to measured consequences.',
          ['oci-20261014-baseline', 'oci-20261015-settlement-batch'], source='Synthetic workflows and simulator effects. Production readiness and comparative savings remain unproven.')

    slide('Why benchmarking matters for this workload',
          [block('A payment workflow can finish quickly and still produce the wrong business effect.', y=139, h=80, size=29, bold=True)],
          'Say: A demo shows that one path can work. Benchmarking asks whether it keeps working under a declared rate, whether a change helps, and whether errors create duplicate effects. We check completed history and an independent ledger, not just successful HTTP starts. The operational decisions are choosing settings, detecting regressions and identifying the next risk to test.',
          ['oci-20261014-baseline', 'oci-20261015-settlement-batch', 'oci-20261018-primary-reboot'],
          tables=[table(['Engineering decision', 'Evidence we need'],
                        [['Choose settings', 'Compare cost of observation with detection delay'],
                         ['Accept a change', 'Preserve correctness and compare against the same reference'],
                         ['Expand the use case', 'Check authorization and recovery separately from throughput']], [330, 534], [40, 64, 64, 64], y=240, size=19)])

    slide('The baseline isolates workflow execution',
          [block('1  Start workflow', y=155, w=280, h=45, size=24, bold=True),
           block('2  Execute task', x=350, y=155, w=260, h=45, size=24, bold=True),
           block('3  Observe history', x=650, y=155, w=262, h=45, size=24, bold=True),
           block('Async reference: deterministic echo tool\nSettlement reference: one simulator ledger effect', y=235, h=85, size=26),
           block('Completion latency includes polling delay. Independent checks then verify the final outcome.', y=345, h=70, size=23, color='576B65'),
           block('Live model calls and reviewer wait time are separate test lanes.', y=445, h=35, size=21, bold=True, color='075B51')],
          'Say: The baseline is the ruler for later changes. Async measures an external task calling a deterministic echo tool. Settlement adds payment policy and a durable simulator effect. A successful start is not a completed payment. Completion time ends at the first durable history observation, includes 100 ms polling, and excludes subsequent oracle verification. p95 means 95 percent of observed completions were at or below that duration. p99 describes the slower one percent boundary. These are not internal engine execution times or live-agent response times.',
          ['oci-20261014-baseline', 'oci-20261015-settlement-batch'], lines=[rule(211)])

    slide('A worker delay constrained the initial setup',
          [block('Before', y=143, w=390, h=35, size=24, bold=True, color='9A5B23'),
           block('100 ms pause after every task', y=190, w=390, h=68, size=28, bold=True),
           block('10/s discovery overloaded the setup.\nPerformance evidence was invalid.', y=285, w=390, h=87, size=22),
           block('After', x=510, y=143, w=402, h=35, size=24, bold=True, color='075B51'),
           block('Immediate fetch after success', x=510, y=190, w=402, h=68, size=28, bold=True),
           block('3,000/3,000 met the two-second\ndeadline at 10/s for five minutes.', x=510, y=285, w=402, h=87, size=22),
           block('Directional improvement in the tested setup. The later 20/s trial still failed.', y=430, h=56, size=21, bold=True)],
          'Say: The first useful result was a harness and worker finding. The old worker slept 100 ms even after successful work, limiting a single worker below ten tasks per second before network and engine work. The changed worker retains idle/error backoff and fetches immediately after success. A later ten-per-second run passed. These exploratory runs retained history without equal checkpoint resets, so we cannot quote a controlled speedup ratio. The twenty-per-second trial had deadline failures and invalid generator accounting. Neither run identifies maximum engine capacity.',
          ['oci-20261013-discovery', 'oci-20261013-worker-pacing'], lines=[dict(x1=480, y1=145, x2=480, y2=388, color='CAD6D0')])

    slide('Less polling reduced traffic and delayed detection',
          [block('9.0% fewer engine requests', y=135, h=55, size=34, bold=True, color='075B51'),
           block('Observed completion p95 increased from about 245 ms to 539 ms.', y=202, h=65, size=25),
           block('Both settings passed 2,400 workflows. The 100 ms default stayed in place.', y=428, h=57, size=21, bold=True)],
          f'Say: We changed only the completion polling interval in a short 100-500-500-100 ms sequence at ten workflows per second. Two runs per setting each completed 1,200 workflows. Exact totals were 13,183 versus 12,000 engine requests, a {engine_reduction:.4f} percent reduction, and 5,983 versus 4,800 history requests, a {history_reduction:.4f} percent reduction. Less frequent polling observes completion later. This is an observed traffic/detection-delay tradeoff, not evidence of slower engine execution. No state resets or randomized repetitions were used, so capacity and precise causal CPU effects remain unproven.',
          ['oci-20261013-polling'], tables=[table(['Polling', 'Engine requests', 'Per-run completion p95'],
                [['100 ms', '13,183', '245.2-245.5 ms'], ['500 ms', '12,000', '538.7-539.1 ms']], [190, 280, 394], [38, 55, 55], y=270, size=20)])

    br = baseline['runs']
    slide('The repeated baseline gives us a stable reference',
          [block('90,000 / 90,000 correct', y=130, h=52, size=36, bold=True, color='075B51'),
           block('Five 30-minute runs at 10 workflows/s. Zero deadline misses, drops or unknown outcomes.', y=191, h=59, size=22),
           block('Same checkpoint before every run. Same code, runtime and observation method.', y=451, h=40, size=19, color='576B65')],
          'Say: We froze the tested configuration after discovery. Each of five repetitions restored the same PostgreSQL/SQLite checkpoint, warmed up for 600 seconds and measured 1,800 seconds at ten per second. Each run completed 18,000 correct outcomes under the two-second deadline. The per-run p95 range was 245.7-249.0 ms and p99 range 247.6-256.1 ms. This provides a reference to detect future regressions. It does not measure maximum capacity. The repetitions share one cell and do not reset host caches or all shared-service history. Never average these percentiles into a pooled p95.',
          ['oci-20261014-baseline'], tables=[table(['Repetition', 'Completion p95', 'Completion p99'],
            [[str(i + 1), f"{r['latency']['completed_observed']['p95'] * 1000:.1f} ms", f"{r['latency']['completed_observed']['p99'] * 1000:.1f} ms"] for i, r in enumerate(br)], [220, 322, 322], [32] + [31] * 5, y=254, size=18)])

    slide('Settlement passed with a similar observed range',
          [block('The workload adds policy checks and a durable synthetic payment effect.', y=138, h=65, size=25),
           block('Both lanes passed 90,000/90,000 at 10/s. No controlled speedup or overhead percentage follows from this comparison.', y=415, h=72, size=22, bold=True)],
          'Say: We then tested a more useful workload. Settlement checks policy and writes a deduplicated simulator ledger effect instead of calling echo. Five thirty-minute repetitions passed 90,000 correct payments. The observed latency ranges overlap the async reference. This supports a separate repeatable settlement reference, not an equivalence or speedup claim. The checkpoint, warmup, workload and worker diagnostics/image differed: async used 600 seconds warmup, settlement 60. We must match those factors in a new A/B experiment to quantify the added overhead.',
          ['oci-20261014-baseline', 'oci-20261015-settlement-batch'],
          tables=[table(['Reference', 'Async echo', 'Synthetic settlement'],
                        [['Per-run p95', '245.7-249.0 ms', '246.1-248.3 ms'], ['Per-run p99', '247.6-256.1 ms', '248.9-255.9 ms'],
                         ['Deadline misses', '0 / 90,000', '0 / 90,000']], [230, 317, 317], [38, 50, 50, 50], y=210, size=19)])

    slide('The six deadline misses remain unresolved',
          [block('6 / 3,000', y=145, w=400, h=62, size=44, bold=True, color='9A5B23'),
           block('Initial settlement run exceeded\nthe two-second deadline.', y=223, w=400, h=80, size=25),
           block('0 / 3,000', x=510, y=145, w=402, h=62, size=44, bold=True, color='075B51'),
           block('Unchanged repeat passed\nafter the same checkpoint reset.', x=510, y=223, w=402, h=80, size=25),
           block('We added worker-stage timing for diagnosis. Later longer runs passed, but no root cause or fix is established.', y=366, h=104, size=25, bold=True)],
          'Say: This is the result that stops us treating a pass as a fix. All three thousand payments settled correctly in the first run, but six were late. An unchanged repeat had no misses. There was no tuning change between those two runs. We then added worker timing and established longer repeated observations. The timing records cover whole client calls and cannot separate network, database commit, response decoding and scheduling. The early gap near settlement completion remains unresolved. Preserve it in the narrative even though later runs passed.',
          ['oci-20261015-settlement', 'oci-20261015-settlement-repeat', 'oci-20261015-worker-timing', 'oci-20261015-settlement-batch'], lines=[rule(337)])

    slide('Native model calls introduce a separate time budget',
          [block('Earlier read limit: 20 seconds', y=141, h=40, size=25, bold=True, color='9A5B23'),
           block('The fifth attempted fixed-input trial stopped on a provider error. Usage remained partly unknown.', y=190, h=62, size=23),
           block('Follow-up: 40-second read, 90-second action budget', y=289, h=40, size=25, bold=True, color='075B51'),
           block('20/20 fixed-input trials passed, using 35 native requests and 10,805 reported tokens.', y=339, h=65, size=23),
           block('A wider timeout budget is a configuration change. Provider tail latency remains unresolved.', y=437, h=52, size=21, bold=True)],
          'Say: The roughly quarter-second workflow result cannot predict an agent that waits on a model. The initial native profile stopped at its fifth attempted trial with a provider error. A follow-up profile allowed forty-second reads within ninety-second actions and completed twenty fixed-input trials, five per case. That is improved observed completion under a different budget, not a controlled proof that the timeout change fixed the provider or improved model quality. Nineteen adversarial fixture cases passed. Six live hostile-note cases were attempted, four completed and two remain inconclusive after provider errors. Do not hide the two inconclusive cases or equate these trials to load benchmarking.',
          ['oci-20261016-live-model-trials', 'oci-20261016-adversarial'], lines=[rule(265)])

    slide('Payment authority stays outside model decisions',
          [block('Model proposes', y=155, w=260, h=38, size=24, bold=True),
           block('Review records a decision', x=350, y=155, w=270, h=70, size=24, bold=True),
           block('Verifier checks authority', x=650, y=155, w=262, h=70, size=24, bold=True),
           block('Trusted payment input', y=244, w=260, h=52, size=21, color='576B65'),
           block('Actual reviewer identity\nand workflow history', x=350, y=244, w=270, h=65, size=21, color='576B65'),
           block('Signed original payment,\nprocess and screening', x=650, y=244, w=262, h=65, size=21, color='576B65'),
           block('Tested changes to payment fields, process binding and reviewer identity created no unauthorized effect.', y=366, h=93, size=27, bold=True, color='075B51')],
          'Say: The functional improvement is adding explicit payment authority. The trusted issuer registers an HMAC-authenticated original payment. The verifier checks its amount, beneficiary, process binding, screening evidence and actual completing reviewer in engine history before settlement. Agents and reviewers do not receive the signing key. The dedicated browser form and persistent verifier passed their declared checks. These are synthetic controls with trusted operators, not production maker/checker certification or a real payment rail. A universal exactly-once guarantee does not follow from the observed no-duplicate cases.',
          ['oci-20261016-bound-payment', 'oci-20261016-payment-form', 'oci-20261016-persistent-verifier'], lines=[rule(329)])

    slide('Recovery tests expanded the verified boundaries',
          [block('The running engine recovered its database connections and preserved the tested business effects.', y=136, h=70, size=27, bold=True),
           block('Both maintenance runs had one HTTP 500 before recovery. Unplanned failover remains open.', y=453, h=45, size=19, color='576B65')],
          'Say: Recovery tests add evidence that a steady-state benchmark cannot supply. We restored coordinated engine, ledger and registry state, validated a managed backup and timestamp-specific PITR, then kept a running isolated engine connected through a planned promotion and a controlled primary NODE_REBOOT. Those last lanes completed thirty-four and one hundred twenty-two workflows respectively with the expected effect counts and no engine restart. They are different low-rate tests, not a reliability improvement ratio. The reboot changed primary identity and all ten JDBC backends started after the request. One HTTP 500 occurred in each maintenance lane. API intervals and sampled success gaps are not application RTO. All eight temporary managed test systems are deleted. Unplanned automatic failover and production HA remain open.',
          ['oci-20261016-coordinated-recovery', 'oci-20261016-pitr-failover', 'oci-20261018-engine-reconnect', 'oci-20261018-primary-reboot'],
          tables=[table(['Boundary', 'Verified outcome'], [['Coordinated store restore', 'Engine, ledger and 28-record authority restored in isolation'],
                ['Managed backup / PITR', '49-table match and timestamp-specific marker behavior'],
                ['Planned replica promotion', '34/34 workflows and expected effects, same engine'],
                ['Controlled primary-node reboot', '122/122 workflows and expected effects, same engine']], [330, 534], [34, 48, 48, 48, 48], y=214, size=18)])

    slide('The results support specific engineering decisions', [],
          'Say: Here is the verdict. The worker change removed an avoidable delay and was followed by a passing discovery run. Less polling saved requests but delayed detection, so we kept the original default. Repeated runs improved confidence in a fixed-rate reference, rather than proving more throughput. Settlement deadline failures and native provider tails remain unresolved. The new controls and recovery cases expand functional evidence. We have not established a vendor advantage, maximum capacity or a quantified end-to-end optimization benefit.',
          ['oci-20261013-worker-pacing', 'oci-20261013-polling', 'oci-20261015-settlement-repeat', 'oci-20261014-baseline', 'oci-20261018-primary-reboot'],
          tables=[table(['Question', 'Verdict', 'Engineering consequence'],
             [['Worker success delay', 'Directional improvement', 'Keep immediate fetch after success'],
              ['Completion polling', 'Measured tradeoff', 'Keep 100 ms until slower detection is acceptable'],
              ['Repeatability at 10/s', 'Reference established', 'Use matched controls for future regressions'],
              ['Settlement tail / model tail', 'Unresolved', 'Retain failures and collect targeted evidence'],
              ['Recovery / payment controls', 'More boundaries tested', 'Advance with the remaining gaps explicit']], [245, 260, 359], [38, 58, 58, 58, 58, 58], y=145, size=18)])

    slide('The next comparison needs one changed factor',
          [block('Question: what overhead does worker timing add?', y=138, h=57, size=29, bold=True),
           block('Candidate: timing enabled vs disabled, using the same worker image, settlement workload and isolated checkpoint.', y=213, h=77, size=25),
           block('Five paired 30-minute runs per setting at 10/s. Fix warmup, polling, JVM settings, resources and background services.', y=313, h=80, size=25),
           block('Prespecified draft, not launched. Fresh cost coverage and isolation checks precede any live run.', y=435, h=52, size=21, color='9A5B23')],
          'Say: To make the next improvement claim credible, we need an explicit A/B question. A useful first comparison is the incremental overhead of worker timing, because that changed between earlier reference lanes. Use the same pinned worker image and vary only the timing flag. Use an isolated owned stack so current payment history and authority are preserved. Freeze five pairs and balanced order before launch, restore the identical compatible checkpoint before each run, and validate golden cases, telemetry and generator accounting in both modes. Report each run, paired differences and failures. This requires fresh whole-campaign budget coverage for the full experiment and cleanup; it is a proposed plan, not a launched or completed test. Unplanned failover and signing-key rotation remain separate functional work.',
          ['oci-20261015-worker-timing', 'oci-20261015-settlement-batch'], source='Proposed next experiment. See validation/local-20261018-comparison-audit/summary.json for fixed controls and launch gates.')

    slide('Appendix: instrumentation overhead remains uncertain',
          [block('Four short runs changed Java-agent attachment only, with the same checkpoint reset.', y=136, h=67, size=25),
           block('The lower sampled CPU with the agent on does not establish that instrumentation improves performance.', y=415, h=73, size=24, bold=True)],
          'Say: This earlier on-off-off-on pilot shows why interpretation matters. Each run passed twelve hundred workflows. CPU means averaged across two runs were 29.87 percent agent-on and 31.62 percent off, while mean-memory ratio was 1.042. JVM restart/warmup effects, few samples and completion polling limit inference. These CPU percentages are unnormalized Docker values for an allocation that can reach 400 percent. We have not established overhead magnitude, equivalence or a capacity gain. Agent-on mode was restored.',
          ['oci-20261014-overhead'], tables=[table(['Java agent', 'Mean run CPU', 'Per-run p95', 'Correct'],
             [['Attached', '29.87%', '248.2-250.3 ms', '2,400/2,400'],
              ['Detached', '31.62%', '247.3-248.6 ms', '2,400/2,400']], [210, 210, 244, 200], [38, 65, 65], y=228, size=20)])

    slide('Appendix: tested allocation and claim boundaries',
          [block('Persistent reference cell', y=139, h=35, size=26, bold=True),
           block('Four fixed Compute VMs and one managed PostgreSQL node\n10 allocated OCPUs and 80 GB memory in Phoenix', y=191, h=80, size=25),
           block('What remains open', y=312, h=35, size=26, bold=True, color='9A5B23'),
           block('Maximum capacity, unplanned failover, signing-key rotation, real identity/payment integration and complete cost attribution.', y=362, h=90, size=25)],
          'Say: The reference is Fluxnova 3.0.1 on private OCI networking. The original managed database remains one node with AD-local storage. Temporary two-node recovery systems do not turn it into a production HA deployment. No autoscaling is configured. The hundred-dollar campaign value is a planning budget, not actual billing or a hard cloud cap. The latest historical reservation covers through 18 October 10:00 UTC, with 24.59 dollars in incomplete posted exact-resource matches. Time and usage change, so recheck before a new allocation. We have no complete dollars-per-payment or comparative savings result. This deck shows a reproducible method and scoped results, not a completed production certification.',
          ['oci-20261014-baseline', 'oci-20261018-primary-reboot'], lines=[rule(286)])

    content = {'title': 'Fluxnova benchmark story: decisions, baseline and changes', 'source_commit': COMMIT,
               'as_of_utc': '2026-10-18', 'font_policy': {'basis': 'design', 'families': ['Arial']},
               'comparison_audit': 'validation/local-20261018-comparison-audit/summary.json', 'slides': slides}
    (ROOT / 'presentations/fluxnova-story.json').write_text(json.dumps(content, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'slides': len(slides), 'audit_status': audit['status'], 'engine_request_reduction_percent': engine_reduction,
                      'history_request_reduction_percent': history_reduction, 'live_systems_mutated': False}))


if __name__ == '__main__':
    main()
