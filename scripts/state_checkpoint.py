#!/usr/bin/env python3
"""Capture, rehearse and restore the dedicated OCI benchmark's two-store state.

Run on the prepared Linux controller. Capture/restore briefly stop the app,
worker and simulator; rehearsal uses a network-disabled disposable PostgreSQL.
No infrastructure or unrelated database schema is deleted.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.checkpoint import digest, identifier, postgres_fingerprint, sqlite_copy, sqlite_fingerprint, restore_pair, restore_listing, RestoreFailure
from benchlib.database import target_fingerprint
from benchlib.deployment import secret
from benchlib.fluxnova import FluxnovaClient
from benchlib.remote import host_target, shell_command, SSH_OPTIONS
from benchlib.util import ROOT, sha256, utc_now, write_json, safe_run_id

CONTAINERS = [('support', 'benchmark-worker'), ('app', 'fluxnova-benchmark'), ('support', 'benchmark-support')]


def checked(argv, *, data=None, env=None, timeout=300):
    result = subprocess.run(argv, input=data, env=env, capture_output=True, timeout=timeout)
    if result.returncode:
        # Do not include Docker environment, connection strings or payloads in exceptions.
        logs=ROOT/'dist/checkpoints/diagnostics';logs.mkdir(mode=0o700,parents=True,exist_ok=True)
        diagnostic=logs/(uuid.uuid4().hex+'.stderr')
        with diagnostic.open('xb') as stream:
            diagnostic.chmod(0o600);stream.write(result.stderr)
        raise RuntimeError('Command failed: ' + argv[0] + ' (exit ' + str(result.returncode) +
                           '); private diagnostic: ' + str(diagnostic))
    return result.stdout


def under(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    if not path.is_relative_to(parent) or path == parent:
        raise ValueError('Checkpoint/receipt path must remain inside its designated workspace directory')
    return path


def load_checkpoint(path):
    manifest = json.loads((path/'manifest.json').read_text())
    if manifest.get('format') != 'fluxnova-pair-v1' or set(manifest['files']) != {'engine.dump', 'ledger.sqlite'}:
        raise ValueError('Unsupported checkpoint manifest')
    for name, checksum in manifest['files'].items():
        if sha256(path/name) != checksum:
            raise ValueError('Checkpoint file checksum mismatch: ' + name)
    if digest(manifest['logical']) != manifest['state_fingerprint']:
        raise ValueError('Checkpoint logical identity mismatch')
    return manifest


class Cell:
    def __init__(self, config, environment, inventory, image, schema):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        self.psycopg, self.config, self.image, self.schema = psycopg, config, image, identifier(schema)
        if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image):
            raise ValueError('PostgreSQL tools require an immutable image digest')
        if inventory.get('status') != 'completed':
            raise ValueError('Completed deployment inventory required')
        for key in ('region', 'compartment_id'):
            if config['campaign'][key] != environment[key]:
                raise ValueError('Deployment/configuration identity mismatch')
        self.targets = {role: host_target(environment['roles'][role]) for role in ('app','support','loadgen')}
        for role, target in self.targets.items():
            if inventory['hosts'][role]['target'] != target:
                raise ValueError('Explicit deployment target mismatch')
        self.spec = config['database_probe']
        self.target = target_fingerprint(self.spec)
        if self.spec['driver'] != 'postgresql' or self.spec['user'] != config['runtime']['db_user']:
            raise ValueError('Checkpoint must use the dedicated application database principal')
        self.parameters = conninfo_to_dict(self.spec['dsn'])
        if self.parameters.get('sslmode') != 'verify-full' or not Path(self.parameters.get('sslrootcert','')).is_file():
            raise ValueError('Verified PostgreSQL TLS required')
        self.password = secret(config, self.spec['password_secret_id'])
        self.engine = FluxnovaClient(config['runtime']['base_url'], config['runtime']['admin_username'],
                                    secret(config, config['runtime']['admin_password_secret_id']))
        self.stopped = []

    def ssh(self, role, argv, data=None, timeout=300):
        return checked(['ssh', *SSH_OPTIONS, self.targets[role], shell_command(argv)], data=data, timeout=timeout)

    def connect(self):
        return self.psycopg.connect(self.spec['dsn'], user=self.spec['user'], password=self.password,
                                   connect_timeout=10, autocommit=True)

    def identities(self):
        result = {}
        for role, name in CONTAINERS:
            value = json.loads(self.ssh(role, ['docker','inspect',name]))[0]
            result[name] = {'host': self.targets[role], 'image': value['Config']['Image']}
            if name == 'benchmark-support':
                mounts = [m for m in value['Mounts'] if m['Destination'] == '/data']
                if len(mounts) != 1 or mounts[0].get('Name') != 'benchmark-ledger':
                    raise ValueError('Unexpected simulator ledger mount')
        return result

    def require_idle_loadgen(self):
        # This host is dedicated to the generator. Docker can display a pinned
        # k6 image as only a short ID, so image-name matching is insufficient.
        if self.ssh('loadgen', ['docker','ps','--quiet']).strip():
            raise ValueError('Dedicated load generator still has running containers')

    def quiesce(self):
        queues = [self.engine.request('GET', route)['count'] for route in
                  ('/process-instance/count','/external-task/count','/incident/count')]
        if any(queues):
            raise ValueError('Active workflows/tasks/incidents must drain before checkpoint operations')
        self.require_idle_loadgen()
        for role, name in CONTAINERS:
            current = json.loads(self.ssh(role,['docker','inspect',name]))[0]
            if not current['State']['Running']:
                raise ValueError('All benchmark writers must initially be running')
        self.require_verifier_fenced()
        for role, name in CONTAINERS:
            self.stopped.append((role,name))
            self.ssh(role,['docker','stop','--time','30',name],timeout=60)
        with self.connect() as db:
            for table in ('act_ru_execution','act_ru_ext_task','act_ru_incident'):
                from psycopg import sql
                count = db.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(sql.Identifier(self.schema),sql.Identifier(table))).fetchone()[0]
                if count:
                    raise ValueError('Work arrived while writers were stopping; checkpoint refused')

    def require_verifier_fenced(self):
        # The original pair format excludes the later persistent authority.
        # Refuse to capture/reset underneath it, including on the legacy CLI.
        from scripts.restore_verifier import FENCE_PROBE, require_stopped
        record = json.loads(self.ssh('support', ['sudo', 'python3', '-'], data=FENCE_PROBE.encode()))
        require_stopped(record, source=record['properties'].get('LoadState') != 'not-found')

    def resume(self):
        # Start the simulator and app before the worker. Restore no unrelated container.
        for role, name in reversed(self.stopped):
            self.ssh(role,['docker','start',name],timeout=60)
        self.stopped.clear()
        for _ in range(90):
            try:
                self.engine.readiness()
                return
            except Exception:
                time.sleep(2)
        raise RuntimeError('Application did not become ready after checkpoint operation')

    def pg_tool(self, args, directory):
        env = os.environ.copy()
        names = {'host':'PGHOST','port':'PGPORT','dbname':'PGDATABASE','sslmode':'PGSSLMODE'}
        for source, target in names.items():
            if source in self.parameters:env[target]=self.parameters[source]
        env.update(PGUSER=self.spec['user'],PGPASSWORD=self.password,PGSSLROOTCERT='/run/db-ca.pem',PGCONNECT_TIMEOUT='10')
        options = []
        for name in ('PGHOST','PGPORT','PGDATABASE','PGSSLMODE','PGUSER','PGPASSWORD','PGSSLROOTCERT','PGCONNECT_TIMEOUT'):
            if name in env:options += ['--env',name]
        return checked(['docker','run','--rm','--network','host','--user',f'{os.getuid()}:{os.getgid()}',
                 '-v',str(Path(self.parameters['sslrootcert']).resolve())+':/run/db-ca.pem:ro',
                 '-v',str(directory.resolve())+':/checkpoint',*options,self.image,*args],env=env,timeout=600)

    def ledger(self, restore=None):
        module = (ROOT/'benchlib/checkpoint.py').read_text()
        code = module + '''
import sys,tempfile
with tempfile.TemporaryDirectory() as temp:
 p=Path(temp)/'ledger.sqlite'
 if sys.argv[1]=='capture':
  fingerprint=sqlite_copy('/data/ledger.sqlite',p)
  print(json.dumps({'logical':fingerprint,'data':base64.b64encode(p.read_bytes()).decode()}))
 else:
  p.write_bytes(sys.stdin.buffer.read())
  fingerprint=sqlite_copy(p,'/data/ledger.sqlite',replace=True)
  print(json.dumps({'logical':fingerprint}))
'''
        image = self.identities()['benchmark-support']['image']
        # SQLite may need to create WAL shared-memory bookkeeping even for a
        # read-only connection. The backup source itself still opens mode=ro.
        output = self.ssh('support',['docker','run','--rm','-i','--network','none','--volumes-from',
                          'benchmark-support:rw',image,'python','-c',code,
                          'restore' if restore else 'capture'],data=restore,timeout=300)
        return json.loads(output)

    def access(self, connection):
        rows = connection.execute('''SELECT c.relname,pg_get_userbyid(c.relowner),
            pg_get_userbyid(a.grantor),CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
            a.privilege_type,a.is_grantable FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,c.relowner))) a
            WHERE n.nspname=%s AND c.relkind IN ('r','S') ORDER BY 1,2,3,4,5,6''',(self.schema,)).fetchall()
        schema = connection.execute('''SELECT pg_get_userbyid(n.nspowner),pg_get_userbyid(a.grantor),
            CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,a.privilege_type,a.is_grantable
            FROM pg_namespace n CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
            WHERE n.nspname=%s ORDER BY 1,2,3,4,5''',(self.schema,)).fetchall()
        defaults = connection.execute('''SELECT pg_get_userbyid(d.defaclrole),d.defaclobjtype,
            pg_get_userbyid(a.grantor),CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
            a.privilege_type,a.is_grantable FROM pg_default_acl d JOIN pg_namespace n ON n.oid=d.defaclnamespace
            CROSS JOIN LATERAL aclexplode(d.defaclacl) a WHERE n.nspname=%s ORDER BY 1,2,3,4,5,6''',(self.schema,)).fetchall()
        return json.loads(json.dumps({'relations':rows,'schema':schema,'defaults':defaults}))

    def capture(self, directory):
        directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        with self.connect() as db:
            before=postgres_fingerprint(db,self.schema)
            access=self.access(db)
        self.pg_tool(['pg_dump','--format=custom','--schema='+self.schema,'--strict-names','--file=/checkpoint/engine.dump'],directory)
        (directory/'engine.dump').chmod(0o600)
        ledger=self.ledger()
        (directory/'ledger.sqlite').write_bytes(base64.b64decode(ledger['data']))
        (directory/'ledger.sqlite').chmod(0o600)
        with self.connect() as db:
            if postgres_fingerprint(db,self.schema)!=before or self.access(db)!=access:
                raise RuntimeError('Engine state changed during quiesced capture')
        logical={'engine':before,'ledger':ledger['logical']}
        manifest={'format':'fluxnova-pair-v1','created_at':utc_now(),'schema':self.schema,'target_fingerprint':self.target,
                  'runtime_images':self.identities(),'postgres_tools_image':self.image,'logical':logical,
                  'state_fingerprint':digest(logical),'access_control':access,
                  'files':{name:sha256(directory/name) for name in ('engine.dump','ledger.sqlite')}}
        write_json(directory/'manifest.json',manifest)
        return manifest

    def validate(self, directory):
        m=load_checkpoint(directory)
        if (m['target_fingerprint'],m['schema'],m['runtime_images'],m['postgres_tools_image']) != (self.target,self.schema,self.identities(),self.image):
            raise ValueError('Checkpoint target, schema, runtime images or tools differ')
        return m

    def restore_engine(self, directory):
        manifest=self.validate(directory)
        with self.connect() as db:
            controls=self.access(db)
        for key in ('schema','defaults'):
            if controls[key]!=manifest['access_control'][key]:
                raise ValueError('Existing schema controls differ; restore requires separate operator review')
        listing=self.pg_tool(['pg_restore','--list','/checkpoint/engine.dump'],directory).decode()
        (directory/'restore.list').write_text(restore_listing(listing,self.schema))
        self.pg_tool(['pg_restore','--clean','--if-exists','--exit-on-error','--single-transaction','--no-owner',
                      '--use-list=/checkpoint/restore.list','--dbname='+self.parameters['dbname'],'/checkpoint/engine.dump'],directory)

    def restore_ledger(self, directory):
        m=self.validate(directory)
        if self.ledger((directory/'ledger.sqlite').read_bytes())['logical']!=m['logical']['ledger']:
            raise RuntimeError('Restored ledger fingerprint mismatch')

    def verify(self, directory):
        m=self.validate(directory)
        with self.connect() as db:
            engine=postgres_fingerprint(db,self.schema)
            if self.access(db)!=m['access_control']:
                raise RuntimeError('Restored schema/table ownership or permissions differ')
        logical={'engine':engine,'ledger':self.ledger()['logical']}
        if logical!=m['logical']:
            raise RuntimeError('Restored pair fingerprint mismatch')
        return digest(logical)


def rehearse(snapshot, image):
    import psycopg
    import tempfile
    from psycopg import sql
    manifest=load_checkpoint(snapshot)
    if image!=manifest['postgres_tools_image']:
        raise ValueError('Rehearsal tools differ from checkpoint')
    name='benchmark-restore-rehearsal-'+uuid.uuid4().hex[:12]
    working=snapshot.parent/('.rehearsal-'+uuid.uuid4().hex)
    working.mkdir(mode=0o700)
    listing=checked(['docker','run','--rm','--network','none','-v',str(snapshot)+':/checkpoint:ro',
                     image,'pg_restore','--list','/checkpoint/engine.dump']).decode()
    (working/'restore.list').write_text(restore_listing(listing,manifest['schema']))
    socket_root=Path(tempfile.mkdtemp(prefix='bench-checkpoint-'))
    sockets=socket_root/'socket';sockets.mkdir();sockets.chmod(0o777)
    started=False
    try:
        checked(['docker','run','-d','--name',name,'--network','none','--cpus','2','--memory','4g',
                 '--tmpfs','/var/lib/postgresql/data:rw,size=3g','-v',str(sockets)+':/var/run/postgresql',
                 '-v',str(snapshot)+':/checkpoint:ro','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_DB=checkpoint',
                 '-v',str(working)+':/rehearsal:ro',
                 image,'postgres','-c',"listen_addresses="],timeout=120)
        started=True
        for _ in range(60):
            try:
                connection=psycopg.connect(host=str(sockets),user='postgres',dbname='checkpoint',autocommit=True,connect_timeout=2)
                break
            except psycopg.OperationalError:time.sleep(1)
        else:raise RuntimeError('Isolated PostgreSQL did not start')
        with closing(connection) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(manifest['schema'])))
            rounds=[]
            for number in range(2):
                checked(['docker','exec',name,'pg_restore','-U','postgres','-d','checkpoint','--clean','--if-exists',
                         '--single-transaction','--exit-on-error','--no-owner','--no-acl',
                         '--use-list=/rehearsal/restore.list','/checkpoint/engine.dump'],timeout=600)
                actual=postgres_fingerprint(db,manifest['schema'])
                if actual!=manifest['logical']['engine']:raise RuntimeError('Isolated PostgreSQL fingerprint mismatch')
                target=working/('ledger-'+str(number)+'.sqlite')
                sqlite_copy(snapshot/'ledger.sqlite',target)
                if sqlite_fingerprint(target)!=manifest['logical']['ledger']:raise RuntimeError('Isolated ledger mismatch')
                if number==0:
                    db.execute(sql.SQL("UPDATE {}.act_ge_property SET value_='checkpoint-rehearsal-change' WHERE name_='schema.version'").format(sql.Identifier(manifest['schema'])))
                    if postgres_fingerprint(db,manifest['schema'])==actual:raise RuntimeError('Rehearsal mutation was not detected')
                    import sqlite3
                    with closing(sqlite3.connect(target)) as ledger:
                        ledger.execute('CREATE TABLE checkpoint_probe (id INTEGER)');ledger.commit()
                    if sqlite_fingerprint(target)==manifest['logical']['ledger']:raise RuntimeError('Ledger mutation was not detected')
                    sqlite_copy(snapshot/'ledger.sqlite',target,replace=True)
                rounds.append({'round':number+1,'engine_matched':True,'ledger_matched':True})
        record={'created_at':utc_now(),'manifest_sha256':sha256(snapshot/'manifest.json'),'state_fingerprint':manifest['state_fingerprint'],
                'passed':True,'restore_strategy':'preserve-schema-v1','rounds':rounds,'scope':'Network-disabled disposable PostgreSQL and SQLite copies; mutations detected and restored; live ACLs checked only during live restore'}
        path=snapshot/'restore-verification-v2.json'
        if path.exists():raise ValueError('Rehearsal receipt already exists')
        write_json(path,record)
        return record
    finally:
        if started:
            checked(['docker','stop','--time','15',name],timeout=30)
            checked(['docker','rm',name],timeout=30)
        if sockets.exists() and not any(sockets.iterdir()):
            sockets.rmdir();socket_root.rmdir()
        # Rehearsal files stay under the private checkpoint directory for audit.


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('capture','rehearse','restore'))
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--environment',type=Path,required=True)
    parser.add_argument('--deployment',type=Path,required=True)
    parser.add_argument('--postgres-image',required=True)
    parser.add_argument('--schema',default='fluxnova')
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-id',default='manual-checkpoint')
    args=parser.parse_args(argv)
    if os.name!='posix':raise ValueError('Checkpoint orchestration runs on the Linux controller')
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Checkpoint interrupted by signal ' + str(signum))
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    import fcntl
    os.umask(0o077)
    home=ROOT/'dist/checkpoints';home.mkdir(mode=0o700,parents=True,exist_ok=True)
    snapshot=under(args.snapshot,home)
    output=under(args.output,ROOT/'dist');output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise ValueError('Receipt output must not already exist')
    run_id=safe_run_id(args.run_id)
    with (home/'.operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.command=='rehearse':
            result=rehearse(snapshot,args.postgres_image)
            write_json(output,result);print(json.dumps(result));return 0
        cell=Cell(json.loads(args.config.read_text()),json.loads(args.environment.read_text()),
                  json.loads(args.deployment.read_text()),args.postgres_image,args.schema)
        if args.command=='restore':
            m=cell.validate(snapshot)
            evidence=json.loads((snapshot/'restore-verification-v2.json').read_text())
            if evidence.get('passed') is not True or evidence.get('restore_strategy')!='preserve-schema-v1' or evidence.get('manifest_sha256')!=sha256(snapshot/'manifest.json') or evidence.get('state_fingerprint')!=m['state_fingerprint']:
                raise ValueError('Matching successful isolated restore verification is required')
        resume=True
        result={'run_id':run_id,'command':args.command,'success':False,'started_at':utc_now()}
        try:
            cell.quiesce()
            if args.command=='capture':
                manifest=cell.capture(snapshot)
                fingerprint=manifest['state_fingerprint']
            else:
                rollback=home/('rollback-'+uuid.uuid4().hex)
                cell.capture(rollback)
                result['rollback_checkpoint']=str(rollback)
                fingerprint=restore_pair(cell.restore_engine,cell.restore_ledger,cell.verify,snapshot,rollback)
            result.update(success=True,state_fingerprint=fingerprint,dataset_sha256=sha256(snapshot/'manifest.json'),
                          method='quiesced-postgresql-schema-and-sqlite-backup',snapshot=str(snapshot))
        except RestoreFailure as exc:
            resume=exc.recovered
            result.update(error_type=type(exc).__name__,rollback_verified=exc.recovered)
            raise
        except BaseException as exc:
            result['error_type']=type(exc).__name__
            raise
        finally:
            try:
                if resume and cell.stopped:cell.resume()
                result['writers_resumed']=resume and not cell.stopped
            except BaseException:
                result.update(success=False,writers_resumed=False,error_type='ResumeFailure')
                raise
            finally:
                result['completed_at']=utc_now();write_json(output,result)
        print(json.dumps(result))
    return 0


if __name__=='__main__':main()
