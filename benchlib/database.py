"""Additional database compatibility evidence; never a substitute for upstream suites."""
import hashlib
import json
import os
import re
import time
import uuid
from .util import utc_now, write_json


def target_fingerprint(spec):
    """Bind probe evidence to its configured target without emitting a DSN."""
    values = {key: spec.get(key) for key in ("driver", "dsn", "user")}
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        raise ValueError("Database target requires driver, credential-free DSN, and user")
    dsn = values["dsn"].strip()
    if "://" in dsn or re.search(r"\b(?:password|passwd|pwd)\s*=", dsn, re.IGNORECASE):
        raise ValueError("Use a DSN without an embedded password or credential-bearing URL")
    normalized = {"driver": values["driver"].strip().lower(), "dsn": dsn, "user": values["user"]}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def transaction_probe(connect):
    """Use a unique scratch table to prove commit, rollback and a fresh connection."""
    table = "BENCH_PROBE_" + uuid.uuid4().hex[:12].upper()
    created = False
    conn = connect()
    fresh = None
    original_error = None
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE TABLE {table} (ID INTEGER PRIMARY KEY, VALUE_TEXT VARCHAR(100))")
        conn.commit()
        created = True
        cur.execute(f"INSERT INTO {table} VALUES (1, 'committed')")
        conn.commit()
        cur.execute(f"INSERT INTO {table} VALUES (2, 'rolled_back')")
        conn.rollback()
        cur.close()
        # Keep the original usable handle until reconnect and cleanup finish.
        # A failed new connection must not strand the committed scratch table.
        fresh = connect()
        cur = fresh.cursor()
        cur.execute(f"SELECT ID, VALUE_TEXT FROM {table} ORDER BY ID")
        rows = cur.fetchall()
        cur.close()
        if rows != [(1, "committed")]:
            raise RuntimeError("Commit/rollback/reconnect assertion failed")
        return {"commit": True, "rollback": True, "fresh_connection": True, "scratch_table": table}
    except BaseException as exc:
        original_error = exc
        raise
    finally:
        cleanup_error = None
        try:
            # End the observer transaction before dropping the table; PostgreSQL
            # SELECT locks can otherwise block cleanup on the original handle.
            if fresh is not None:
                fresh.rollback()
            conn.rollback()
            if created:
                with conn.cursor() as cleanup:
                    cleanup.execute(f"DROP TABLE {table}")
                conn.commit()
        except Exception as exc:
            cleanup_error = exc
        finally:
            for connection in (fresh, conn):
                if connection is not None:
                    try:
                        connection.close()
                    except Exception as exc:
                        cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            if original_error is not None:
                original_error.add_note("Database probe cleanup also failed: " + type(cleanup_error).__name__)
            else:
                raise cleanup_error


def check_database(config, output):
    spec = config["database_probe"]
    fingerprint = target_fingerprint(spec)
    password = os.environ.get(spec.get("password_env", "BENCH_DB_PASSWORD"))
    if password is None and spec.get("password_secret_id"):
        from .deployment import secret
        password = secret(config, spec["password_secret_id"])
    if not password:
        raise ValueError("Database probe requires a password through an environment variable or Vault reference")
    driver, dsn = spec["driver"], spec["dsn"]
    if driver == "postgresql":
        import psycopg
        if "sslmode=verify-full" not in dsn or "sslrootcert=" not in dsn:
            raise ValueError("Managed PostgreSQL probe requires sslmode=verify-full and sslrootcert")
        connect = lambda: psycopg.connect(dsn, user=spec["user"], password=password, connect_timeout=10)
        version_query = "SELECT version()"
        driver_version = psycopg.__version__
    elif driver == "oracle":
        import oracledb
        connect = lambda: oracledb.connect(user=spec["user"], password=password, dsn=dsn, **spec.get("connection_options", {}))
        version_query = "SELECT banner_full FROM v$version"
        driver_version = oracledb.__version__
    else:
        raise ValueError("Database probe supports postgresql or oracle only")
    started = time.monotonic()
    result = {"created_at": utc_now(), "driver": driver, "driver_version": driver_version, "target_fingerprint": fingerprint,
              "scope": "Additional benchmark database tests; upstream suite coverage remains separate",
              "migration_test": "not executed; requires a retained previous-release schema fixture",
              "network_interruption_test": "not executed; fresh connection is not outage recovery"}
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(version_query)
                result["actual_server_version"] = [row[0] for row in cur.fetchall()]
                cur.execute("SELECT NAME_, VALUE_ FROM ACT_GE_PROPERTY WHERE NAME_ IN ('schema.version', 'schema.history')")
                result["fluxnova_schema_properties"] = dict(cur.fetchall())
                if "schema.version" not in result["fluxnova_schema_properties"]:
                    raise RuntimeError("Fluxnova schema version missing")
        result["transactions"] = transaction_probe(connect)
        result["status"] = "pass"
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        write_json(output, result)
        raise RuntimeError(f"Database compatibility probe failed ({type(exc).__name__}); see evidence file") from None
    result["elapsed_s"] = time.monotonic() - started
    write_json(output, result)
    return result
