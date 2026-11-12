import json
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from services.cluster_probe import ProbeStore, make_server
from services.transport_diagnostics import TransportDiagnostics
from scripts.diagnose_fnv07_transport import diagnose


class TransportDiagnosticsTests(unittest.TestCase):
    def test_diagnostic_refuses_changed_sealed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / 'engine-n1.log'
            evidence.write_bytes(b'original')
            (root / 'checksums.json').write_text(json.dumps({'files': {
                evidence.name: hashlib.sha256(evidence.read_bytes()).hexdigest()}}))
            evidence.write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError, 'evidence_seal_mismatch'):
                diagnose(root)

    def test_explicit_close_is_optional_and_returns_valid_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProbeStore(Path(tmp) / 'probe.sqlite', 'run-fixture', ['n1'])
            for close in (False, True):
                with self.subTest(connection_close=close):
                    server = make_server(store, admin_token='admin-fixture', execute_token='execute-fixture',
                                         connection_close=close)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/snapshot', data=b'{}',
                                                     headers={'X-Fnv07-Token': 'admin-fixture'})
                        with urllib.request.urlopen(req, timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            self.assertEqual(json.load(response), store.snapshot())
                            if close:
                                self.assertEqual(response.headers['Connection'], 'close')
                    finally:
                        server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_trace_captures_handler_failure_without_credentials_or_exception_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'transport.ndjson'
            store = ProbeStore(Path(tmp) / 'probe.sqlite', 'run-fixture', ['n1'])
            secret = 'private-fixture-do-not-log'
            server = make_server(store, admin_token=secret, execute_token='execute-fixture', diagnostics_path=path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(store, 'snapshot', side_effect=RuntimeError(secret)):
                    request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/snapshot', data=b'{}',
                                                     headers={'X-Fnv07-Token': secret})
                    with self.assertRaises(Exception):
                        urllib.request.urlopen(request, timeout=3)
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/snapshot', data=b'{}',
                                                 headers={'X-Fnv07-Token': secret})
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)
            text = path.read_text()
            self.assertNotIn(secret, text)
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertEqual([r['seq'] for r in rows], list(range(1, len(rows) + 1)))
            self.assertTrue(any(r['event'] == 'handler_error' and r['error_class'] == 'RuntimeError' for r in rows))
            self.assertTrue(any(r['event'] == 'response_sent' and r['status'] == 200 for r in rows))

    def test_writer_rejects_extra_fields_and_never_logs_unknown_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.ndjson'
            writer = TransportDiagnostics(path)
            with self.assertRaises(ValueError):
                writer.emit('request_received', headers='secret')
            writer.emit('request_received', route='/secret?token=private')
            self.assertNotIn('secret', path.read_text())
            self.assertNotIn('private', path.read_text())
            with self.assertRaises(FileExistsError):
                TransportDiagnostics(path)
