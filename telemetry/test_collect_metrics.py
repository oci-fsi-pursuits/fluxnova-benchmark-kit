import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.collect_metrics import host_metrics, docker_metrics, size_bytes, postgres_metrics, main


class SnapshotTests(unittest.TestCase):
    def test_host_counters_and_docker_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stat").write_text("cpu 10 0 5 80 5 0 0 0 0 0\n")
            (root / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 600 kB\n")
            (root / "loadavg").write_text("0.1 0.2 0.3 1/2 3\n")
            metrics = host_metrics(root)
            self.assertEqual(metrics["host_cpu_total_ticks"], 100)
            self.assertEqual(metrics["host_memory_available_bytes"], 614400)
        self.assertEqual(size_bytes("1.5GiB"), 1.5 * 1024**3)
        self.assertEqual(size_bytes("2MB"), 2000000)
        with self.assertRaises(ValueError):
            size_bytes("unavailable")

    def test_container_arguments_are_not_shell_commands(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(stdout=json.dumps({"CPUPerc": "12.5%", "MemUsage": "1MiB / 2GiB", "PIDs": "11"}))
        metrics = docker_metrics("fluxnova", run)
        self.assertEqual(metrics["container_cpu_percent"], 12.5)
        self.assertNotIn("shell", calls[0][1])
        with self.assertRaises(ValueError):
            docker_metrics("fluxnova; printenv", run)

    def test_missing_linux_metrics_fail_without_fabricated_zero_or_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdout", new_callable=io.StringIO) as output:
            status = main(["--host", "--proc-root", directory])
        record = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertNotIn("host_cpu_total_ticks", record)
        self.assertEqual(record["_meta"]["errors"]["host"], "FileNotFoundError")

    def test_postgres_enforces_readonly_tls_and_never_outputs_password(self):
        class Cursor:
            def __init__(self):
                self.queries = []
                self.rows = iter([(4, 10, 1, 2, 90, 0, 0), (2, 0, 0.25), (3, 1, 0.5), (1, 0), (5, 3, 1)])
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql): self.queries.append(sql)
            def fetchone(self): return next(self.rows)
        cursor = Cursor()
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def cursor(self): return cursor
        calls = []
        def connect(**kwargs):
            calls.append(kwargs)
            return Connection()
        with patch.dict(os.environ, {"BENCH_DB_PASSWORD": "NEVER-OUTPUT-THIS"}):
            record = postgres_metrics({"host": "db.internal", "port": 5432, "database": "fluxnova", "user": "metrics", "ca_file": "/protected/ca.pem"}, connect=connect, engine=True)
        self.assertIn("default_transaction_read_only=on", calls[0]["options"])
        self.assertEqual(calls[0]["sslmode"], "verify-full")
        self.assertTrue(all(query.startswith("SELECT ") for query in cursor.queries))
        self.assertNotIn("NEVER-OUTPUT-THIS", json.dumps(record))
        self.assertEqual(record["db_connections"], 4)
        self.assertEqual(record['engine_external_tasks'], 5)
        self.assertEqual(record['engine_external_tasks_unlocked'], 3)
        self.assertEqual(record['engine_external_tasks_exhausted'], 1)


if __name__ == "__main__":
    unittest.main()
