"""Logical fingerprints and checked SQLite copies for benchmark checkpoints.

PostgreSQL fingerprints cover ordinary tables, columns, constraints, indexes and
sequences in one dedicated schema. Ownership/ACLs are retained in pg_dump but are
not part of the portable logical fingerprint. Unsupported schema objects fail.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', value):
        raise ValueError('Expected a simple lowercase database identifier')
    if value == 'public' or value.startswith(('pg_', 'information_schema')):
        raise ValueError('System/shared schemas are outside checkpoint scope')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def restore_listing(listing, schema):
    """Retain table/data entries while preserving the existing schema controls."""
    identifier(schema)
    prefixes = ('SCHEMA - '+schema+' ', 'ACL - SCHEMA '+schema+' ',
                'COMMENT - SCHEMA '+schema+' ', 'SECURITY LABEL - SCHEMA '+schema+' ',
                'DEFAULT ACL '+schema+' ')
    lines=[]
    for line in listing.splitlines():
        match=re.match(r'^\d+;\s+\d+\s+\d+\s+(.*)$',line)
        if match and match[1].startswith(prefixes):
            line='; preserved existing schema control: '+line
        lines.append(line)
    return '\n'.join(lines)+'\n'


def _rows(rows):
    count, result = 0, hashlib.sha256()
    for row in rows:
        if not isinstance(row, str):
            row = json.dumps([{'bytes': base64.b64encode(v).decode()} if isinstance(v, bytes) else v
                              for v in row], ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        data = row.encode()
        result.update(len(data).to_bytes(8, 'big'))
        result.update(data)
        count += 1
    return {'rows': count, 'sha256': result.hexdigest()}


def sqlite_fingerprint(path):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite foreign-key check failed')
        objects = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables = {}
        quote = lambda x: '"' + x.replace('"', '""') + '"'
        for kind, name, _, ddl in objects:
            if kind != 'table':
                continue
            if ddl and 'CREATE VIRTUAL TABLE' in ddl.upper():
                raise ValueError('Virtual SQLite tables are outside checkpoint scope')
            columns = [r[1] for r in db.execute('PRAGMA table_info(' + quote(name) + ')')]
            order = ','.join(quote(c) + ' COLLATE BINARY' for c in columns)
            tables[name] = _rows(db.execute('SELECT * FROM ' + quote(name) + ' ORDER BY ' + order))
        result = {'format': 'sqlite-logical-v1', 'objects': objects, 'tables': tables}
        # Tuples are normalized to JSON arrays so receipts round-trip identically.
        result = json.loads(json.dumps(result))
        result['sha256'] = digest(result)
        return result


def sqlite_copy(source, destination, *, replace=False):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if source == destination:
        raise ValueError('Source and destination must differ')
    expected = sqlite_fingerprint(source)
    if not replace:
        with destination.open('xb'):
            pass
        destination.chmod(0o600)
    elif not destination.is_file():
        raise ValueError('Restore target must already exist')
    # The online backup API includes committed WAL content; raw file copying does not.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
    if sqlite_fingerprint(destination) != expected:
        raise RuntimeError('SQLite copy fingerprint mismatch')
    return expected


def postgres_fingerprint(connection, schema):
    from psycopg import sql
    identifier(schema)
    if connection.info.transaction_status != 0:
        raise ValueError('Fingerprint requires an idle connection')
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        connection.execute("SET LOCAL bytea_output = 'hex'")
        connection.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(schema)))
        objects = connection.execute('''SELECT c.relname,c.relkind FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname''', (schema,)).fetchall()
        if not objects or any(kind not in ('r', 'i', 'S') for _, kind in objects):
            raise ValueError('Expected a nonempty schema with ordinary tables/indexes/sequences only')
        functions = connection.execute('''SELECT count(*) FROM pg_proc p JOIN pg_namespace n
            ON n.oid=p.pronamespace WHERE n.nspname=%s''', (schema,)).fetchone()[0]
        triggers = connection.execute('''SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal''', (schema,)).fetchone()[0]
        if functions or triggers:
            raise ValueError('Custom functions/triggers are outside checkpoint scope')
        columns = connection.execute('''SELECT c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),
            a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=%s AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum''', (schema,)).fetchall()
        constraints = connection.execute('''SELECT c.relname,k.conname,pg_get_constraintdef(k.oid)
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s ORDER BY c.relname,k.conname''', (schema,)).fetchall()
        indexes = connection.execute('''SELECT c.relname,pg_get_indexdef(c.oid) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='i'
            ORDER BY c.relname''', (schema,)).fetchall()
        tables, sequences = {}, {}
        for name, kind in objects:
            if kind == 'r':
                with connection.cursor(name='checkpoint_rows') as cursor:
                    cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text COLLATE "C"')
                                   .format(sql.Identifier(schema), sql.Identifier(name)))
                    tables[name] = _rows(row[0] for row in cursor)
            elif kind == 'S':
                sequences[name] = connection.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}')
                                                     .format(sql.Identifier(schema), sql.Identifier(name))).fetchone()
        result = {'format': 'postgres-logical-v1', 'schema': schema, 'columns': columns,
                  'constraints': constraints, 'indexes': indexes, 'tables': tables, 'sequences': sequences}
        result = json.loads(json.dumps(result))
        result['sha256'] = digest(result)
        return result


class RestoreFailure(RuntimeError):
    def __init__(self, recovered):
        self.recovered = recovered
        super().__init__('Reset failed; previous state restored' if recovered else
                         'Reset and rollback failed; keep writers stopped and use retained rollback checkpoint')


def restore_pair(restore_engine, restore_ledger, verify, checkpoint, rollback):
    """Resume writers only after success or verified rollback of both stores."""
    try:
        restore_engine(checkpoint)
        restore_ledger(checkpoint)
        return verify(checkpoint)
    except BaseException:
        # A transactional engine failure may leave both stores untouched.
        # Verify that before attempting any additional destructive operation.
        try:
            verify(rollback)
        except BaseException:
            try:
                restore_engine(rollback)
                restore_ledger(rollback)
                verify(rollback)
            except BaseException:
                raise RestoreFailure(False) from None
        raise RestoreFailure(True) from None
