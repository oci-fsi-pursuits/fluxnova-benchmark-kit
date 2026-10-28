import tempfile
import unittest
from pathlib import Path

from benchlib.otel_files import last_complete_jsonl_offset


class CollectorFileBoundaryTests(unittest.TestCase):
    def test_partial_batch_larger_than_64_kib_preserves_previous_complete_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'metrics.jsonl'
            prefix = b'{"resourceMetrics":[]}\n'
            path.write_bytes(prefix + b'{"partial":"' + b'x' * 200000)
            self.assertEqual(last_complete_jsonl_offset(path), len(prefix))

    def test_complete_large_batch_includes_entire_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'traces.jsonl'
            content = b'{"value":"' + b'x' * 200000 + b'"}\n'
            path.write_bytes(content)
            self.assertEqual(last_complete_jsonl_offset(path), len(content))

    def test_offsets_use_bytes_and_find_newlines_at_block_edges(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'export.jsonl'
            prefix = '{"value":"\u20ac"}\n'.encode()
            for size in (1, 2, 7, len(prefix), 65536):
                path.write_bytes(prefix + b'x' * (size * 2))
                self.assertEqual(last_complete_jsonl_offset(path, block_bytes=size), len(prefix))

    def test_missing_record_boundary_stays_invalid(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'export.jsonl'
            for content in (b'', b'{}', b'x' * 200000):
                path.write_bytes(content)
                with self.assertRaisesRegex(ValueError, 'no complete'):
                    last_complete_jsonl_offset(path)
            for size in (0, -1, True, 1.5):
                with self.assertRaisesRegex(ValueError, 'positive integer'):
                    last_complete_jsonl_offset(path, block_bytes=size)
