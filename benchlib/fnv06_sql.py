"""Acquire a read-only durable-state snapshot from an isolated FNV-06 database.

Acceptance and REST/SQL reconciliation live in the separate evidence auditor.
No ORM, workflow REST endpoint or collector-provided row selection is used here.
"""
from datetime import datetime, timezone


TABLES = ('act_hi_procinst', 'act_hi_actinst', 'act_hi_varinst', 'act_ru_execution',
          'act_ru_event_subscr', 'act_hi_batch', 'act_ru_batch', 'act_ru_job', 'act_ru_incident')


def capture(db, *, run_id, target_id, expected_user, bound):
    result = {'study_id': 'FNV-06', 'run_id': run_id, 'target_id': target_id,
              'started_at': datetime.now(timezone.utc).isoformat(), 'tables': {}}
    with db.transaction():
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        db.execute("SET LOCAL TIME ZONE 'UTC'")
        identity = db.execute("SELECT current_database(), current_schema(), current_user, "
                              "current_setting('transaction_read_only'), current_setting('transaction_isolation'), "
                              "current_setting('TimeZone'), ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        assert identity == ('postgres', 'fluxnova', expected_user, 'on', 'repeatable read', 'UTC', True)
        result['connection'] = dict(zip(('database', 'schema', 'user', 'read_only', 'isolation', 'timezone', 'tls'), identity))
        for table in TABLES:
            bound()
            query = 'SELECT * FROM fluxnova.' + table + ' ORDER BY id_'
            cursor = db.execute(query)
            columns = [c.name for c in cursor.description]
            rows = []
            for raw in cursor.fetchall():
                row = {}
                for name, value in zip(columns, raw, strict=True):
                    # Engine timestamp columns are naive PostgreSQL timestamps; the JVM is pinned to UTC.
                    if isinstance(value, datetime):
                        value = value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()
                    row[name] = value
                rows.append(row)
            count_query = 'SELECT count(*) FROM fluxnova.' + table
            count = db.execute(count_query).fetchone()[0]
            assert count == len(rows)
            result['tables'][table] = {'query': query, 'count_query': count_query, 'count': count, 'rows': rows}
        bound()
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    return result
