import unittest
import hashlib
import json
from pathlib import Path
import tempfile
from scripts.run_fnv07_http_probe import reconcile, audit_probe


class HttpReconciliationTests(unittest.TestCase):
    def events(self, ids):
        return [dict(event=event, request_id=i) for i in ids
                for event in ('request_received', 'response_sent')]

    def test_exact_client_and_server_population_required(self):
        rows = '0\tok\t200\t10\tnone\n1\tok\t200\t15\tnone\n'
        self.assertTrue(reconcile(rows, self.events([0, 1]), 2)['all_roundtrips_verified'])
        for events in (self.events([0]), self.events([0, 0, 1]), self.events([0, 1, 2])):
            self.assertFalse(reconcile(rows, events, 2)['all_roundtrips_verified'])

    def test_failures_and_missing_client_rows_cannot_disappear(self):
        failed = '0\tok\t200\t10\tnone\n1\terror\t0\t15\tIOException:EOFException\n'
        report = reconcile(failed, self.events([0]), 2)
        self.assertFalse(report['all_roundtrips_verified'])
        self.assertTrue(report['client_denominator_complete'])
        self.assertEqual(report['client_ids_without_server_request'], [1])
        self.assertEqual(report['client_error_classes'], {'IOException:EOFException': 1})
        incomplete = reconcile('0\tok\t200\t10\tnone\n', self.events([0]), 2)
        self.assertFalse(incomplete['client_denominator_complete'])
        self.assertFalse(incomplete['all_roundtrips_verified'])

    def test_duplicate_client_ids_cannot_satisfy_denominator(self):
        report = reconcile('0\tok\t200\t10\tnone\n0\tok\t200\t15\tnone\n', self.events([0, 1]), 2)
        self.assertFalse(report['client_denominator_complete'])

    def test_saved_auditor_recomputes_failures_even_if_summary_is_resealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def save(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
            def seal():
                save(root / 'checksums.json', {'files': {
                    p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob('*') if p.is_file() and p.name != 'checksums.json'}})
            for name in ('classes/HttpProbeClient.class', 'inputs/services/fnv07/HttpProbeClient.java',
                         'inputs/scripts/run_fnv07_http_probe.py'):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
            save(root / 'manifest.json', {'repeats': 1, 'requests_per_arm': 2, 'source_sha256': {}})
            results = []
            for close in (False, True):
                arm = root / 'pair-1-' / ('explicit-close' if close else 'default')
                arm.mkdir(parents=True)
                client = ('0\tok\t200\t10\tnone\n1\tok\t200\t15\tnone\n' if close else
                          '0\tok\t200\t10\tnone\n1\terror\t0\t15\tIOException\n')
                events = [dict(seq=i + 1, **event) for i, event in enumerate(self.events([0, 1] if close else [0]))]
                (arm / 'client.tsv').write_text(client)
                (arm / 'transport.ndjson').write_text('\n'.join(json.dumps(e) for e in events))
                cleanup = dict(owned_server_stopped=True, client_exit_code=0)
                save(arm / 'cleanup.json', cleanup)
                result = reconcile(client, events, 2)
                result.update(pair=1, mode='explicit_close' if close else 'default', **cleanup)
                save(arm / 'summary.json', result); results.append(result)
            save(root / 'summary.json', dict(planned_arms=2, completed_arms=2, arms=results))
            seal()
            audited = audit_probe(root)
            self.assertFalse(audited['arms'][0]['all_roundtrips_verified'])
            self.assertTrue(audited['arms'][1]['all_roundtrips_verified'])
            results[0]['client_outcomes'] = {'ok': 2}
            save(root / 'pair-1-/default/summary.json', results[0])
            seal()
            with self.assertRaisesRegex(ValueError, 'changed_probe_summary'):
                audit_probe(root)
