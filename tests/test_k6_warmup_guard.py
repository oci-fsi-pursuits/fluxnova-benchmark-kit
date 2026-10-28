"""Optional real-k6 checks against a synthetic loopback HTTP server.

Set BENCH_TEST_K6_BINARY to a k6 executable (or container wrapper).
These fixtures do not measure Fluxnova performance.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.environ.get('BENCH_TEST_K6_BINARY'), 'requires an explicit k6 executable')
class WarmupGuardIntegrationTests(unittest.TestCase):
    def exercise(self, mode, guard, settings=None):
        keys = {}
        starts, polls = {}, []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if mode == 'start_failure':
                    return self.reply(503, {})
                key = body['businessKey']
                keys[key] = key
                starts[key] = time.monotonic()
                self.reply(200, {'id': key})

            def do_GET(self):
                key = self.path.rsplit('/', 1)[-1]
                polls.append((key, time.monotonic() - starts.get(key, time.monotonic())))
                self.reply(200, {'businessKey': keys.get(key), 'state': 'COMPLETED',
                                 'endTime': '2026-01-01T00:00:00Z' if mode == 'healthy' or
                                 (mode == 'delayed' and time.monotonic() - starts[key] >= .15) else None})

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(dir=os.environ.get('BENCH_TEST_TMP_ROOT')) as temp:
                env = {k: v for k, v in os.environ.items() if not k.startswith('BENCH_')}
                env.update(BENCH_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                           BENCH_RUN_ID='synthetic-warmup-guard', BENCH_SCENARIO='minimal',
                           BENCH_RATE='1', BENCH_DURATION_S='2', BENCH_WARMUP_S='6',
                           BENCH_DRAIN_S='1', BENCH_DEADLINE_S='1', BENCH_PREALLOCATED_VUS='4',
                           BENCH_MAX_VUS='4', BENCH_OUTPUT_DIR=temp)
                if guard is not None:
                    env['BENCH_ABORT_ON_WARMUP_UNKNOWN'] = str(guard).lower()
                env.update(settings or {})
                script = Path(__file__).resolve().parents[1] / 'load/benchmark.js'
                proc = subprocess.run([os.environ['BENCH_TEST_K6_BINARY'], 'run', '--log-format', 'json',
                                       '--no-color', str(script)], env=env, capture_output=True,
                                      text=True, timeout=30)
                events = []
                for line in (proc.stdout + '\n' + proc.stderr).splitlines():
                    try:
                        message = json.loads(line).get('msg', '')
                        if message.startswith('BENCH_EVENT '):
                            events.append(json.loads(message[len('BENCH_EVENT '):]))
                    except (ValueError, AttributeError):
                        pass
                summary_path = Path(temp) / 'k6-summary.json'
                self.assertTrue(summary_path.exists(), proc.stdout + '\n' + proc.stderr)
                summary = json.loads(summary_path.read_text())
                summary['_fixture_history_polls'] = polls
                return proc.returncode, events, summary
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_timeout_aborts_before_measurement_and_retains_event(self):
        code, events, summary = self.exercise('timeout', True)
        self.assertEqual(code, 108)
        self.assertTrue(any(e['event'] == 'outcome' and e['details']['reason'] == 'completion_observation_timeout' for e in events))
        self.assertFalse(any(e['phase'] == 'measurement' for e in events))
        self.assertTrue(summary['warmup_guard']['abort_on_unknown'])

    def test_unconfirmed_start_also_aborts(self):
        code, events, _ = self.exercise('start_failure', True)
        self.assertEqual(code, 108)
        self.assertTrue(any(e['event'] == 'error' for e in events))
        self.assertFalse(any(e['phase'] == 'measurement' for e in events))

    def test_healthy_warmup_reaches_measurement(self):
        code, events, _ = self.exercise('healthy', True)
        self.assertEqual(code, 0)
        self.assertEqual(sum(e['phase'] == 'measurement' and e['event'] == 'completed' for e in events), 2)

    def test_default_preserves_existing_unknown_behavior(self):
        code, events, summary = self.exercise('timeout', None)
        self.assertEqual(code, 0)
        self.assertTrue(any(e['phase'] == 'measurement' for e in events))
        self.assertFalse(summary['warmup_guard']['abort_on_unknown'])

    def test_late_polling_reduces_requests_without_changing_unknown_outcome(self):
        settings = dict(BENCH_WARMUP_S='0', BENCH_DURATION_S='1', BENCH_DRAIN_S='2',
                        BENCH_DEADLINE_S='0.2', BENCH_POLL_INTERVAL_MS='50')
        code, events, control = self.exercise('timeout', None, settings)
        self.assertEqual(code, 0)
        self.assertTrue(any(x['event'] == 'outcome' and x['status'] == 'unknown' for x in events))
        settings['BENCH_LATE_POLL_INTERVAL_MS'] = '500'
        code, events, slower = self.exercise('timeout', None, settings)
        self.assertEqual(code, 0)
        self.assertTrue(any(x['event'] == 'outcome' and x['status'] == 'unknown' for x in events))
        self.assertLess(len(slower['_fixture_history_polls']), len(control['_fixture_history_polls']) / 2)
        self.assertEqual(slower['polling_policy']['after_deadline_interval_ms'], 500)

    def test_late_policy_preserves_predeadline_observation(self):
        settings = dict(BENCH_WARMUP_S='0', BENCH_DURATION_S='1', BENCH_DRAIN_S='2',
                        BENCH_DEADLINE_S='0.8', BENCH_POLL_INTERVAL_MS='50', BENCH_LATE_POLL_INTERVAL_MS='500')
        code, events, summary = self.exercise('delayed', None, settings)
        self.assertEqual(code, 0)
        completed = [x for x in events if x['event'] == 'completed']
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]['status'], 'correct')
        self.assertLess(completed[0]['latency_s'], .8)
        self.assertGreaterEqual(len(summary['_fixture_history_polls']), 3)


if __name__ == '__main__':
    unittest.main()
