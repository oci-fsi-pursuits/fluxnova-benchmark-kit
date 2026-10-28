#!/usr/bin/env python3
"""One read-only Linux/container/PostgreSQL snapshot as flat numeric JSON.

Unavailable metrics are omitted, never replaced with zero. The harness can use
required_metrics to reject missing evidence. Diagnostics contain exception classes,
not connection strings, SQL text, database rows, password values or subprocess stderr.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


def host_metrics(proc_root=Path("/proc")):
    root = Path(proc_root)
    ticks = [int(value) for value in (root / "stat").read_text().splitlines()[0].split()[1:9]]
    if len(ticks) < 8:
        raise ValueError("Incomplete CPU counters")
    memory = {}
    for line in (root / "meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024
    return {
        "host_cpu_total_ticks": sum(ticks),
        "host_cpu_idle_ticks": ticks[3] + ticks[4],
        "host_memory_total_bytes": memory["MemTotal"],
        "host_memory_available_bytes": memory.get("MemAvailable", memory.get("MemFree", 0)),
        "host_load_1m": float((root / "loadavg").read_text().split()[0]),
    }


def size_bytes(value):
    found = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*", value)
    units = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
             "TB": 1000**4, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    if not found or found[2] not in units:
        raise ValueError("Unknown Docker size unit")
    return float(found[1]) * units[found[2]]


def docker_metrics(container, run=subprocess.run):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
        raise ValueError("Invalid container identifier")
    response = run(["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
                   capture_output=True, text=True, check=True, timeout=3)
    rows = [json.loads(line) for line in response.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise ValueError("Exactly one requested container sample is required")
    row = rows[0]
    used, limit = row["MemUsage"].split("/")
    return {
        "container_cpu_percent": float(row["CPUPerc"].rstrip("%")),
        "container_memory_used_bytes": size_bytes(used),
        "container_memory_limit_bytes": size_bytes(limit),
        "container_pids": int(row["PIDs"]),
    }


def postgres_metrics(settings, connect=None, engine=False):
    if connect is None:
        import psycopg
        connect = psycopg.connect
    password = os.environ.get("BENCH_DB_PASSWORD")
    if not password:
        raise ValueError("Database password environment variable is required")
    # No DSN/URL is accepted, so callers cannot accidentally echo credentials in argv.
    connection = connect(host=settings["host"], port=settings["port"], dbname=settings["database"],
                         user=settings["user"], password=password, sslmode="verify-full",
                         sslrootcert=settings["ca_file"], connect_timeout=2, autocommit=True,
                         options="-c default_transaction_read_only=on -c statement_timeout=500 -c lock_timeout=250")
    metrics = {}
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, deadlocks, temp_bytes FROM pg_stat_database WHERE datname = current_database()")
            values = cursor.fetchone()
            if values is None:
                raise ValueError("Database statistics row unavailable")
            keys = ["db_connections", "db_commits_total", "db_rollbacks_total", "db_blocks_read_total", "db_blocks_hit_total", "db_deadlocks_total", "db_temp_bytes_total"]
            metrics.update({key: value for key, value in zip(keys, values) if value is not None})
            cursor.execute("SELECT count(*) FILTER (WHERE state = 'active'), count(*) FILTER (WHERE wait_event_type = 'Lock'), coalesce(extract(epoch FROM clock_timestamp() - min(xact_start)), 0) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()")
            metrics.update(zip(["db_active_connections", "db_lock_waiting_connections", "db_oldest_transaction_age_s"], cursor.fetchone()))
            if engine:
                # The Fluxnova 3.x default PostgreSQL schema. No query of variable payloads.
                cursor.execute("SELECT count(*), count(*) FILTER (WHERE retries_ > 0 AND (duedate_ IS NULL OR duedate_ <= current_timestamp) AND (lock_exp_time_ IS NULL OR lock_exp_time_ < current_timestamp)), coalesce(max(extract(epoch FROM current_timestamp - duedate_)) FILTER (WHERE retries_ > 0 AND duedate_ <= current_timestamp AND (lock_exp_time_ IS NULL OR lock_exp_time_ < current_timestamp)), 0) FROM act_ru_job")
                metrics.update(zip(["engine_jobs_total", "engine_due_unlocked_jobs", "engine_oldest_due_unlocked_job_age_s"], cursor.fetchone()))
                cursor.execute("SELECT (SELECT count(*) FROM act_ru_incident), (SELECT count(*) FROM act_ru_task)")
                metrics.update(zip(["engine_incidents", "engine_human_tasks"], cursor.fetchone()))
                cursor.execute("SELECT count(*), count(*) FILTER (WHERE (retries_ IS NULL OR retries_ > 0) AND (lock_exp_time_ IS NULL OR lock_exp_time_ < current_timestamp)), count(*) FILTER (WHERE retries_ = 0) FROM act_ru_ext_task")
                metrics.update(zip(["engine_external_tasks", "engine_external_tasks_unlocked", "engine_external_tasks_exhausted"], cursor.fetchone()))
    return {key: float(value) for key, value in metrics.items() if value is not None}


def collect(args):
    result = {"_meta": {"schema_version": "1.0", "observed_at": datetime.now(timezone.utc).isoformat(),
                        "errors": {}, "requested_sources": [], "read_only": True}}
    actions = []
    if args.host:
        actions.append(("host", lambda: host_metrics(args.proc_root)))
    if args.container:
        actions.append(("docker", lambda: docker_metrics(args.container)))
    if args.postgres:
        actions.append(("postgres", lambda: postgres_metrics({"host": args.db_host, "port": args.db_port,
            "database": args.db_name, "user": args.db_user, "ca_file": args.db_ca_file}, engine=args.engine)))
    for name, action in actions:
        result["_meta"]["requested_sources"].append(name)
        try:
            values = action()
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values.values()):
                raise ValueError("Nonfinite or nonnumeric metric")
            result.update(values)
        except Exception as exc:
            result["_meta"]["errors"][name] = type(exc).__name__
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="store_true")
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--container", help="One explicit container name or ID; no shell evaluation")
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--engine", action="store_true", help="Also query default Fluxnova PostgreSQL job/incident/task tables")
    parser.add_argument("--db-host", default=os.environ.get("BENCH_DB_HOST"))
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", default=os.environ.get("BENCH_DB_NAME", "fluxnova"))
    parser.add_argument("--db-user", default=os.environ.get("BENCH_DB_USER", "benchmetrics"))
    parser.add_argument("--db-ca-file", default=os.environ.get("BENCH_DB_CA_FILE"))
    args = parser.parse_args(argv)
    if not (args.host or args.container or args.postgres):
        parser.error("Select at least one of --host, --container, --postgres")
    if args.engine and not args.postgres:
        parser.error("--engine requires --postgres")
    if args.postgres and not (args.db_host and args.db_ca_file):
        parser.error("PostgreSQL collection requires --db-host and --db-ca-file or their BENCH environment variables")
    result = collect(args)
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))
    return 1 if result["_meta"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
