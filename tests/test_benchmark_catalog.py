from pathlib import Path
import tempfile
import unittest
from benchlib.catalog import load_catalog, validate_catalog, explain, markdown, html_guide
from benchlib.util import ROOT


class BenchmarkCatalogTests(unittest.TestCase):
    def setUp(self):
        self.data = load_catalog()

    def test_retained_catalogue_and_generated_guides(self):
        self.assertEqual(validate_catalog(self.data)['illustrated_patterns'], 9)
        self.assertEqual((ROOT/'docs/benchmark-guide.md').read_text(encoding='utf-8'), markdown(self.data))
        self.assertEqual((ROOT/'docs/benchmark-guide.html').read_text(encoding='utf-8'), html_guide(self.data))

    def test_functional_result_cannot_be_promoted_to_measured(self):
        explain('W03', self.data)['claim_level'] = 'measured'
        with self.assertRaisesRegex(ValueError, 'Claim exceeds'):
            validate_catalog(self.data)

    def test_missing_limit_is_rejected(self):
        explain('W02', self.data)['limits'] = ''
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            validate_catalog(self.data)

    def test_altered_evidence_digest_is_rejected(self):
        explain('W02', self.data)['evidence'][0]['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'Evidence changed'):
            validate_catalog(self.data)

    def test_missing_evidence_is_rejected(self):
        explain('W02', self.data)['evidence'][0]['path'] = 'validation/not-a-study/summary.json'
        with self.assertRaisesRegex(ValueError, 'Missing or unsafe'):
            validate_catalog(self.data)

    def test_path_traversal_is_rejected(self):
        explain('W02', self.data)['evidence'][0]['path'] = '../outside.json'
        with self.assertRaisesRegex(ValueError, 'escapes'):
            validate_catalog(self.data)

    def test_unmapped_pattern_is_rejected(self):
        self.data['patterns'][0]['studies'] = ['UNKNOWN']
        with self.assertRaisesRegex(ValueError, 'unknown study'):
            validate_catalog(self.data)

    def test_unreviewed_missing_diagram_is_rejected(self):
        self.data['patterns'].pop()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            validate_catalog(self.data)

    def test_unknown_experiment_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, 'Unknown catalogue'):
            explain('unknown')

    def test_guide_escapes_untrusted_text(self):
        self.data['studies'][0]['name'] = '<script>alert(1)</script>'
        result = html_guide(self.data)
        self.assertNotIn('<script>alert(1)</script>', result)
        self.assertIn('&lt;script&gt;', result)

    def test_explicit_archive_does_not_relax_pinning(self):
        from scripts.run_w02_functional import distribution_inventory
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'untrusted.zip'
            path.write_bytes(b'unverified distribution')
            with self.assertRaisesRegex(AssertionError, 'distribution_archive_not_verified'):
                distribution_inventory(Path(td), path)

    def test_transient_handoff_access_denial_keeps_deadline_and_guard(self):
        from unittest.mock import Mock, patch
        from benchlib.fnv06_telemetry import await_file
        guard = Mock()
        with patch('benchlib.fnv06_telemetry.read', side_effect=[PermissionError(), {'ready': True}]):
            self.assertEqual(await_file(Path('handoff'), bound=guard, timeout=1), {'ready': True})
        guard.assert_called_once()
        with patch('benchlib.fnv06_telemetry.read', side_effect=PermissionError()):
            with self.assertRaises(TimeoutError):
                await_file(Path('handoff'), bound=guard, timeout=.01)

    def test_malformed_handoff_fails_without_retry(self):
        import json
        from unittest.mock import Mock, patch
        from benchlib.fnv06_telemetry import await_file
        guard = Mock()
        with patch('benchlib.fnv06_telemetry.read', side_effect=json.JSONDecodeError('invalid', '', 0)) as read:
            with self.assertRaises(json.JSONDecodeError):
                await_file(Path('handoff'), bound=guard, timeout=1)
            self.assertEqual(read.call_count, 1)
        guard.assert_not_called()
