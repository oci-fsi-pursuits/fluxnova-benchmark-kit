"""Protocol/accounting tests. Synthetic rows below are not benchmark findings."""
from collections import Counter
import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchlib.analysis import distribution
from benchlib.fx_quotes import select_quote
from benchlib.pay05_performance import resolve_corpus, designs, strata, worker_assessment, summarize_pairs, qualified_gate, MODE
from scripts.audit_pay05_performance import seal, verify_seal, check_store, sha
from services.fx_quotes import validate_case, QuoteStore
from benchlib.checkpoint import sqlite_copy

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    protocol = json.loads((ROOT / 'workloads/pay05/performance-protocol.json').read_text())
    base = json.loads((ROOT / 'workloads/pay05/cases.json').read_text())
    corpus = resolve_corpus(protocol, base)
    qplan, mplan = designs(protocol, corpus, 'a' * 32)
    return protocol, base, corpus, qplan, mplan


def record(plan, name, arm, *, cycles=6):
    cohorts, profiles = {}, {}
    for phase, n in [('warmup', 1), ('measurement', cycles)]:
        p = {}
        for profile in plan['profile_ids']:
            # Declared artificial test numbers: one profile misses its deadline
            # in the sequential fixture. These are not measured engine timings.
            latency = 3 if arm == 'sequential' and profile == 'all_providers_slow' else 1
            categories = {k: {'expected': m * n, 'verified': m * n, 'within_deadline': m * n if latency <= 2 else 0,
                             'observed_latency_s': distribution([latency] * (m * n))}
                          for k, m in [('settled', 17), ('rejected', 1), ('blocked', 4)]}
            p[profile] = {'verified': 22 * n, 'outcomes': categories}
        profiles[phase] = p
        outcomes = {}
        for kind in ('settled', 'rejected', 'blocked'):
            outcomes[kind] = {key: sum(p[x]['outcomes'][kind][key] for x in p) for key in ('expected', 'verified', 'within_deadline')}
            outcomes[kind]['observed_latency_s'] = distribution([1] * outcomes[kind]['verified'])
        cohorts[phase] = {'offered': 66 * n, 'correct': 66 * n, 'dropped': 0, 'invalid_or_unknown': [], 'outcomes': outcomes,
                          'on_time_correct': sum(r['within_deadline'] for r in outcomes.values())}
    return {'folder': name, 'arm': arm, 'status': 'passed', 'issues': [], 'offered_rate': .5, 'case_mode': MODE,
            'cohorts': cohorts, 'profile_outcomes': profiles, 'worker_attempts': {'status': 'passed'}}


class Pay05PerformanceTests(unittest.TestCase):
    def test_trace_is_balanced_stable_and_changes_only_declared_delays(self):
        protocol, base, corpus, _, _ = inputs(); original = copy.deepcopy(base)
        self.assertEqual(corpus, resolve_corpus(protocol, base)); self.assertEqual(base, original)
        self.assertEqual(len(corpus['cases']), 66)
        self.assertEqual(Counter(c['profile_id'] for c in corpus['cases']), Counter({p['id']: 22 for p in protocol['profiles']}))
        for case in corpus['cases']:
            validate_case(case)
            source = next(c for c in base['cases'] if c['case_id'] == case['base_case_id'])
            restored = copy.deepcopy(case)
            restored['case_id'] = restored.pop('base_case_id'); restored.pop('profile_id')
            if 'require_parallel_overlap' in source: restored['require_parallel_overlap'] = source['require_parallel_overlap']
            else: restored.pop('require_parallel_overlap')
            for route in ('a', 'b', 'c'): restored['quotes'][route]['delay_ms'] = source['quotes'][route]['delay_ms']
            self.assertEqual(restored, source)

    def test_declared_fixed_outcomes_stay_valid_through_the_expected_delay_window(self):
        _, _, corpus, _, _ = inputs()
        for case in corpus['cases']:
            for ms in (0, 600, 1400, 2700, 5000, 10000):
                with self.subTest(case=case['case_id'], decision_ms=ms):
                    quotes = {r: q['quote'] if q['outcome'] == 'quote' else None for r, q in case['quotes'].items()}
                    selected = select_quote(case['payment'], quotes, ms, minimum_valid_quotes=case['minimum_valid_quotes'])
                    self.assertEqual({k: selected[k] for k in case['expected']}, case['expected'])

    def test_protocol_and_source_tampering_rejects_before_allocation(self):
        for field in ('base', 'profile', 'rate'):
            protocol, base, _, _, _ = inputs()
            if field == 'base': base['cases'][0]['payment']['authorized'] = not base['cases'][0]['payment']['authorized']
            elif field == 'profile': protocol['profiles'][0]['delay_ms']['a'] = True
            else: protocol['rate_per_s'] = 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                corpus = resolve_corpus(protocol, base); designs(protocol, corpus, 'a' * 32)

    def test_design_fixes_denominators_and_counterbalances_pair_order(self):
        protocol, _, corpus, qplan, plan = inputs()
        self.assertEqual((qplan['warmup_arrivals'], qplan['measurement_arrivals']), (66, 66))
        self.assertEqual((plan['warmup_s'], plan['measurement_s'], plan['measurement_arrivals']), (132, 792, 396))
        self.assertEqual(Counter(p[0] for p in plan['pairs']), Counter(sequential=3, parallel=2))
        self.assertEqual((qplan, plan), designs(protocol, corpus, 'a' * 32))
        self.assertNotEqual(plan['pair_order_seed'], designs(protocol, corpus, 'b' * 32)[1]['pair_order_seed'])

    def test_qualification_cannot_skip_an_arm_or_replace_failure_with_late_success(self):
        _, _, _, plan, _ = inputs()
        rows = [record(plan, 'q' + str(i) + '-' + lane['arm'], lane['arm'], cycles=1) for i, lane in enumerate(plan['lanes'], 1)]
        self.assertTrue(qualified_gate(rows, plan))
        for changed in (rows[:1], rows[::-1], rows + rows[:1]):
            with self.assertRaises(ValueError): qualified_gate(changed, plan)
        rows[0]['cohorts']['measurement']['correct'] -= 1
        with self.assertRaises(ValueError): qualified_gate(rows, plan)

    def test_primary_comparison_counts_only_timely_settlements(self):
        _, _, _, _, plan = inputs()
        rows = [record(plan, f'p{i}-{j}-{arm}', arm) for i, pair in enumerate(plan['pairs'], 1) for j, arm in enumerate(pair, 1)]
        result = summarize_pairs(plan, rows)
        self.assertTrue(result['performance_comparison_complete'])
        self.assertAlmostEqual(result['comparison']['median_paired_change_in_timely_settlements_per_s'], 102 / 792)
        self.assertEqual(result['comparison']['outcomes']['sequential']['settled']['verified'], 1530)
        self.assertEqual(result['comparison']['outcomes']['parallel']['settled']['within_deadline'], 1530)
        self.assertFalse(summarize_pairs(plan, rows[:-1])['performance_comparison_complete'])
        for mutation in ('duplicate', 'order', 'profile_count', 'profile_time', 'profile_missing', 'worker', 'cohort'):
            changed = copy.deepcopy(rows)
            if mutation == 'duplicate': changed.append(changed[0])
            elif mutation == 'order': changed[0], changed[1] = changed[1], changed[0]
            elif mutation == 'profile_count': changed[0]['profile_outcomes']['measurement']['balanced']['outcomes']['settled']['expected'] += 1
            elif mutation == 'profile_time': changed[0]['profile_outcomes']['measurement']['balanced']['outcomes']['settled']['observed_latency_s']['p95'] = float('nan')
            elif mutation == 'profile_missing': changed[0]['profile_outcomes']['warmup'].pop('balanced')
            elif mutation == 'worker': changed[0]['worker_attempts']['status'] = 'failed'
            else: changed[0]['cohorts']['measurement']['on_time_correct'] += 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): summarize_pairs(plan, changed)

    def test_worker_reports_must_cover_current_execution_and_all_unique_tasks(self):
        execution = {'started_at': '2026-10-31T01:00:01+00:00', 'finished_at': '2026-10-31T01:10:00+00:00'}
        worker = {'type': 'pay05_worker_run', 'execution_id': 'c' * 32, 'worker_slots': 3, 'idle_s': .1,
                  'started_at': '2026-10-31T01:00:00+00:00', 'finished_at': '2026-10-31T01:10:01+00:00',
                  'records': [{'status': 'completed', 'operation': k, 'task_id': k} for k in ('quote_a', 'quote_b', 'quote_c', 'select')]}
        self.assertEqual(worker_assessment(worker, execution, 1)['status'], 'passed')
        for mutation in ('stale', 'duplicate', 'missing', 'operation', 'fatal'):
            changed = copy.deepcopy(worker)
            if mutation == 'stale': changed['finished_at'] = '2026-10-31T01:09:59+00:00'
            elif mutation == 'duplicate': changed['records'][0]['task_id'] = changed['records'][1]['task_id']
            elif mutation == 'missing': changed['records'].pop()
            elif mutation == 'operation': changed['records'][0]['operation'] = 'select'
            else: changed['records'].append({'status': 'fatal_worker_error'})
            with self.subTest(mutation=mutation): self.assertEqual(worker_assessment(changed, execution, 1)['status'], 'failed')

    def test_profile_statistics_exclude_invalid_rows_without_losing_denominators(self):
        _, _, corpus, _, _ = inputs(); case = corpus['cases'][0]
        row = {'event': 'completed', 'phase': 'measurement', 'logical_request_id': 'bad', 'latency_s': 99,
               'details': {'original': case}}
        result = strata([row], corpus, {'cohorts': {'measurement': {'offered': 66, 'invalid_or_unknown': [{'logical_request_id': 'bad'}]}}})
        self.assertEqual(sum(p['verified'] for p in result['measurement'].values()), 0)
        self.assertEqual(sum(r['expected'] for p in result['measurement'].values() for r in p['outcomes'].values()), 66)

    def test_seal_covers_nested_seals_and_detects_missing_extra_or_modified_evidence(self):
        for mutation in ('extra', 'missing', 'nested'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); lane = root / 'lane'; lane.mkdir(); (lane / 'data.json').write_text('{}')
                seal(lane); seal(root); self.assertEqual(verify_seal(root), 2)
                if mutation == 'extra': (root / 'extra').write_text('x')
                elif mutation == 'missing': (lane / 'data.json').unlink()
                else: (lane / 'checksums.json').write_text('{}')
                with self.assertRaises(AssertionError): verify_seal(root)

    def test_real_sqlite_backup_and_journal_inventory_remain_bound_to_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source.sqlite'; QuoteStore(source)
            folder = root / 'snapshot'; folder.mkdir()
            logical = sqlite_copy(source, folder / 'study.sqlite')
            (folder / 'engine.dump').write_bytes(b'Synthetic PostgreSQL placeholder; no database result')
            (folder / 'logical.json').write_text(json.dumps({'backend': logical}))
            (folder / 'files.json').write_text(json.dumps({p.name: sha(p) for p in folder.iterdir()}))
            check_store(folder)
            (folder / 'engine.dump').write_bytes(b'tampered')
            with self.assertRaises(AssertionError): check_store(folder)


if __name__ == '__main__': unittest.main()
