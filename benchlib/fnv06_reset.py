"""Reset only an independently audited cell in a fresh, owned FNV-06 database.

The operator must first obtain the target inventory from a signed OCI GET and
retain/download the previous cell. This module never accepts a schema name or
an arbitrary role. DROP SCHEMA CASCADE can affect dependencies in other schemas;
outside-schema catalog inventories must therefore match before commit.
https://www.postgresql.org/docs/current/sql-dropschema.html
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from benchlib.fnv06_cloud import require_owned_database, require_stage


RUNTIME_TABLES=('act_ru_execution','act_ru_event_subscr','act_ru_batch','act_ru_job',
                'act_ru_incident','act_ru_ext_task','act_ru_task')
OUTSIDE_QUERIES={
    'schemas': "SELECT oid,nspname,nspowner FROM pg_namespace WHERE nspname<>'fluxnova' ORDER BY oid",
    'relations': "SELECT c.oid,c.relnamespace,c.relname,c.relkind,c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname<>'fluxnova' ORDER BY c.oid",
    'functions': "SELECT p.oid,p.pronamespace,p.proname,p.proowner,p.proargtypes::text,p.prorettype FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname<>'fluxnova' ORDER BY p.oid",
    'types': "SELECT t.oid,t.typnamespace,t.typname,t.typowner,t.typtype,t.typrelid,t.typbasetype FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname<>'fluxnova' ORDER BY t.oid",
    'constraints': "SELECT c.oid,c.connamespace,c.conname,c.conrelid,c.confrelid,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname<>'fluxnova' ORDER BY c.oid",
    'triggers': "SELECT t.oid,t.tgrelid,t.tgname,t.tgfoid FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname<>'fluxnova' ORDER BY t.oid",
}

# TOAST tables and their indexes live in pg_toast, but are storage belonging
# to their parent table. Enumerate exact catalog links, never exclude pg_toast
# wholesale: https://www.postgresql.org/docs/current/storage-toast.html
STORAGE_QUERY="""SELECT p.oid,t.oid,p.relowner,t.relowner,n.nspname,t.relkind,
                       i.indexrelid,x.relowner,nx.nspname,x.relkind
FROM pg_class p JOIN pg_namespace np ON np.oid=p.relnamespace
JOIN pg_class t ON t.oid=p.reltoastrelid JOIN pg_namespace n ON n.oid=t.relnamespace
JOIN pg_index i ON i.indrelid=t.oid JOIN pg_class x ON x.oid=i.indexrelid
JOIN pg_namespace nx ON nx.oid=x.relnamespace
WHERE np.nspname='fluxnova' ORDER BY p.oid,i.indexrelid"""


def owned_storage(db):
    result=set();parents=set()
    for parent,toast,owner,toast_owner,namespace,kind,index,index_owner,index_namespace,index_kind in db.execute(STORAGE_QUERY).fetchall():
        if (not all(type(v) is int and v>0 for v in (parent,toast,owner,index))
                or parent in parents or toast in result or index in result or toast==index
                or owner!=toast_owner or owner!=index_owner
                or namespace!='pg_toast' or index_namespace!='pg_toast' or kind!='t' or index_kind!='i'):
            raise ValueError('Owned TOAST storage identity differs')
        parents.add(parent);result.update((toast,index))
    return result


def outside_catalogs(db,storage):
    result={}
    for name,query in OUTSIDE_QUERIES.items():
        rows=db.execute(query).fetchall()
        # Only native storage whose exact parent is in the trial schema is
        # excluded. A foreign view/constraint/function remains protected.
        position={'relations':0,'types':5,'constraints':3,'triggers':1}.get(name)
        result[name]=[r for r in rows if not storage or position is None or r[position] not in storage]
    return result


def verify_seal(folder):
    folder=Path(folder).resolve();seal=folder/'checksums.json'
    if seal.is_symlink():raise ValueError('Symlinked evidence seal')
    raw=seal.read_bytes();inventory=json.loads(raw)
    if inventory.get('algorithm')!='sha256':raise ValueError('Unexpected evidence checksum algorithm')
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p.name!='checksums.json'}
    if actual!=set(inventory['files']):raise ValueError('Evidence file population changed')
    for name,digest in inventory['files'].items():
        path=folder/name
        if (not path.resolve().is_relative_to(folder) or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest()!=digest):
            raise ValueError('Evidence bytes or path changed')
    return hashlib.sha256(raw).hexdigest()


def require_previous(meta,target,summary,audit,seal_sha256):
    run_id=require_stage(meta,meta['stage'])
    require_owned_database(target,source_id=meta['source_id'],target_id=meta['target_id'],run_id=run_id)
    if target.get('lifecycle_state')!='ACTIVE':raise ValueError('Owned database is not active')
    cells=[{'id':'C'+str(i+1).zfill(2),'kind':kind,'population':n} for i,(kind,n) in
           enumerate((kind,n) for kind in ('message','signal','batch') for n in (10,1000))]
    previous=meta.get('qualification_cell')
    if meta.get('qualification_protocol')!='repeat' or previous not in cells[:-1]:
        raise ValueError('No eligible next qualification cell')
    count=100+previous['population']+4
    if (summary.get('study_id')!='FNV-06-REPEAT-QUAL' or summary.get('run_id')!=run_id
            or summary.get('status')!='collected_pending_independent_audit'
            or summary.get('owned_engine_stopped') is not True or summary.get('cleanup_errors')!=[]
            or len(summary.get('owned_process_ids',[]))!=count
            or len(set(summary['owned_process_ids']))!=count
            or audit.get('study_id')!='FNV-06-REPEAT-QUAL' or audit.get('status')!='passed'
            or audit.get('qualification_cell')!=previous or audit.get('workflow_instances')!=count
            or audit.get('expected_completions')!=count-4 or audit.get('unchanged_waiting_controls')!=4
            or audit.get('cloud_resources_accessed') is not True
            or audit.get('database_audit',{}).get('tables_reconciled')!=9
            or audit.get('warmup',{}).get('status')!='passed'
            or not re.fullmatch('[0-9a-f]{64}',seal_sha256)
            or audit.get('source_checksums_sha256')!=seal_sha256):
        raise ValueError('Previous cell is not independently audited and stopped')
    return cells[cells.index(previous)+1]


def reset_owned_schema(db,*,meta,target,summary,audit,seal_sha256,bound):
    next_cell=require_previous(meta,target,summary,audit,seal_sha256)
    role='fnv06_'+meta['run_id'][:12]
    if (db.info.host!=meta['connection']['primary_db_endpoint']['fqdn'] or db.info.dbname!='postgres'
            or db.info.port!=5432 or db.autocommit is not True or int(db.info.transaction_status)!=0):
        raise ValueError('Connection is not idle and bound to the owned target')
    bound()
    with db.transaction():
        db.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        db.execute("SET LOCAL lock_timeout='10s'");db.execute("SET LOCAL statement_timeout='60s'")
        identity=db.execute("SELECT current_database(),current_user,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity!=('postgres',meta['admin_username'],True):raise ValueError('Database identity or TLS differs')
        owner=db.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='fluxnova'").fetchone()
        if owner!=(meta['admin_username'],):raise ValueError('Schema owner differs')
        privileges=db.execute('SELECT rolname,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        if privileges!=(role,False,False,False):raise ValueError('Owned role differs')
        if db.execute('SELECT count(*) FROM pg_stat_activity WHERE usename=%s',(role,)).fetchone()[0]:
            raise ValueError('Owned engine is still connected')
        db.execute('LOCK TABLE fluxnova.act_hi_procinst IN ACCESS EXCLUSIVE MODE')
        history=db.execute('SELECT id_,end_time_ FROM fluxnova.act_hi_procinst ORDER BY id_').fetchall()
        if (len(history)!=len(summary['owned_process_ids']) or {r[0] for r in history}!=set(summary['owned_process_ids'])
                or any(r[1] is None for r in history)):
            raise ValueError('Previous owned history or terminal state differs')
        for table in RUNTIME_TABLES:
            if db.execute('SELECT count(*) FROM fluxnova.'+table).fetchone()[0]:
                raise ValueError('Runtime work remains before reset')
        storage=owned_storage(db)
        outside=outside_catalogs(db,storage)
        bound()
        db.execute('DROP SCHEMA fluxnova CASCADE')
        # The role is constructed exclusively from a validated 32-digit hex run ID.
        db.execute('DROP ROLE "'+role+'"')
        if db.execute("SELECT count(*) FROM pg_namespace WHERE nspname='fluxnova'").fetchone()[0]:
            raise ValueError('Schema remains')
        if db.execute('SELECT count(*) FROM pg_roles WHERE rolname=%s',(role,)).fetchone()[0]:
            raise ValueError('Owned role remains')
        if storage and db.execute('SELECT count(*) FROM pg_class WHERE oid=ANY(%s)',(sorted(storage),)).fetchone()[0]:
            raise ValueError('Owned TOAST storage remains')
        if outside_catalogs(db,storage)!=outside:
            raise ValueError('Reset would change objects outside the owned schema; transaction rolled back')
        bound()
    return {'at':datetime.now(timezone.utc).isoformat(),'status':'passed','run_id':meta['run_id'],
        'target_id':meta['target_id'],'previous_cell':meta['qualification_cell'],'next_cell':next_cell,
        'prior_workflow_instances':len(history),'prior_seal_sha256':seal_sha256,
        'schema_and_owned_role_absent':True,'outside_schema_catalogs_unchanged':True,
        'owned_toast_relations_removed':len(storage),
        'source_reference_distinct':True,'transaction_committed':True}
