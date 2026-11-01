"""Read-only PostgreSQL evidence for the FNV-07 functional corpus."""
from datetime import datetime, timezone

TABLES = ('act_hi_procinst', 'act_hi_actinst', 'act_hi_varinst', 'act_ru_execution',
          'act_ru_event_subscr', 'act_ru_job', 'act_ru_incident', 'act_hi_job_log')


def capture(db, *, run_id, expected_database, expected_user, bound):
    result = {'study_id': 'FNV-07-FUNCTIONAL-1', 'run_id': run_id,
              'started_at': datetime.now(timezone.utc).isoformat(), 'tables': {}}
    with db.transaction():
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        db.execute("SET LOCAL TIME ZONE 'UTC'")
        identity = db.execute("SELECT current_database(), current_schema(), current_user, "
            "current_setting('transaction_read_only'), current_setting('transaction_isolation'), "
            "current_setting('TimeZone'), ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        assert identity == (expected_database, 'fluxnova', expected_user, 'on', 'repeatable read', 'UTC', True)
        result['connection'] = dict(zip(('database', 'schema', 'user', 'read_only', 'isolation', 'timezone', 'tls'), identity))
        for table in TABLES:
            bound()
            query = 'SELECT * FROM fluxnova.' + table + ' ORDER BY id_'
            cursor = db.execute(query)
            names = [column.name for column in cursor.description]
            rows = []
            for values in cursor.fetchall():
                row = dict(zip(names, values, strict=True))
                for name, value in row.items():
                    if isinstance(value, datetime):
                        row[name] = value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()
                rows.append(row)
            count_query = 'SELECT count(*) FROM fluxnova.' + table
            count = db.execute(count_query).fetchone()[0]
            assert len(rows) == count
            result['tables'][table] = {'query': query, 'count_query': count_query, 'count': count, 'rows': rows}
        bound()
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    return result
