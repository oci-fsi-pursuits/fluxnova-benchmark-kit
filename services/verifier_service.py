"""Persistent single-owner payment intent authority/verifier; loopback API only.

Loads one explicitly pinned Vault secret with a Compute instance principal.
The secret supplies the HMAC key, management token and dedicated engine identity.
No operator OCI profile or credential file is used on the host.
"""
import argparse
import base64
import hmac
import json
import os
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import unquote,urlsplit

from services.bound_payment import IntentRegistry,BoundWorker
from services.recoverable_agent import atomic_json,owner
from services.worker import Client
from services.signing_keys import SigningKeys
from services.http_support import drain_rejected_body


def decode_secret(values):
    common={'version','management_token','engine_username','engine_password','support_token'}
    version=values.get('version') if isinstance(values,dict) else None
    if type(version) is not int or version not in (1,2,3):raise ValueError('invalid_verifier_secret')
    required=common|({'signing_key_b64'} if version==1 else
                    {'signing_keys_b64','active_signing_key_id','legacy_signing_key_id'})
    if version==3:required|={'revoked_signing_key_ids','retired_signing_keys_sha256'}
    if set(values)!=required:raise ValueError('invalid_verifier_secret')
    if any(not isinstance(values[k],str) or not values[k] for k in common-{'version'}):
        raise ValueError('invalid_verifier_secret')
    if len(values['management_token'])<32:raise ValueError('invalid_verifier_key_material')
    try:
        if version==1:
            key=base64.b64decode(values['signing_key_b64'],validate=True)
            if len(key)!=32:raise ValueError('invalid_verifier_key_material')
        else:
            encoded=values['signing_keys_b64']
            if not isinstance(encoded,dict):raise ValueError('invalid_signing_key_ring')
            if version==3 and (not isinstance(values['revoked_signing_key_ids'],list)
                               or not isinstance(values['retired_signing_keys_sha256'],dict)):
                raise ValueError('invalid_signing_key_lifecycle_policy')
            key=SigningKeys({k:base64.b64decode(v,validate=True) for k,v in encoded.items()},
                            values['active_signing_key_id'],values['legacy_signing_key_id'],
                            revoked=values['revoked_signing_key_ids'] if version==3 else (),
                            retired=values['retired_signing_keys_sha256'] if version==3 else None)
    except (TypeError,ValueError):raise ValueError('invalid_verifier_key_material') from None
    return values,key


def load_vault(config):
    if type(config['secret_version']) is not int or config['secret_version']<1:raise ValueError('invalid_vault_version')
    import oci
    signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client=oci.secrets.SecretsClient({'region':config['region']},signer=signer,timeout=(10,30),retry_strategy=oci.retry.NoneRetryStrategy())
    bundle=client.get_secret_bundle(config['secret_id'],version_number=config['secret_version'],retry_strategy=oci.retry.NoneRetryStrategy()).data
    if type(bundle.version_number) is not int or bundle.version_number!=config['secret_version']:
        raise ValueError('vault_version_mismatch')
    values=json.loads(base64.b64decode(bundle.secret_bundle_content.content))
    return decode_secret(values)


def make_api(registry,token,health,port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,status,value):
            raw=json.dumps(value,separators=(',',':')).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):pass
        def authorized(self):
            return hmac.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+token).encode())
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/health':return self.reply(200 if health['ready'] else 503,dict(health))
            if not self.authorized():return self.reply(401,{'error':'unauthorized'})
            if path.startswith('/intents/'):
                try:
                    record=registry.load(unquote(path[len('/intents/'):]))
                    return self.reply(200,{'key':record['body']['key'],'process_id':record['process_id'],'screening_id':record['screening_id'],
                        'screening_key':record['screening_key'],'expires_utc_s':record['body']['expires_utc_s']})
                except ValueError:return self.reply(404,{'error':'unknown_intent'})
            self.reply(404,{'error':'not_found'})
        def do_POST(self):
            if not self.authorized():
                drain_rejected_body(self,16384)
                return self.reply(401,{'error':'unauthorized'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=16384:return self.reply(413,{'error':'invalid_size'})
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('object_required')
                path=urlsplit(self.path).path
                if path=='/intents':
                    if set(body)!={'request_id','payment','reviewer','ttl_s'}:raise ValueError('invalid_issuance_fields')
                    if not isinstance(body['request_id'],str):raise ValueError('issuance_request_id_required')
                    return self.reply(200,registry.issue(body['payment'],body['reviewer'],body['ttl_s'],request_id=body['request_id']))
                if path.startswith('/intents/') and path.endswith('/bind'):
                    if set(body)!={'process_id','screening_id','screening_key'}:raise ValueError('invalid_binding_fields')
                    key=unquote(path[len('/intents/'):-len('/bind')]);registry.bind(key,**body)
                    return self.reply(200,{'bound':True,'key':key})
                self.reply(404,{'error':'not_found'})
            except (ValueError,KeyError,TypeError):self.reply(409,{'error':'invalid_or_conflicting_request'})
            except Exception:self.reply(503,{'error':'authority_unavailable'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def serve(config,secret_loader=load_vault,test_checkpoints=None):
    state=Path(config['state_directory']);state.mkdir(parents=True,exist_ok=True)
    stopping=threading.Event()
    with owner(state):
        values,key=secret_loader(config)
        registry=IntentRegistry(state/'intents.sqlite',key)
        authorization='Basic '+base64.b64encode((values['engine_username']+':'+values['engine_password']).encode()).decode()
        engine=Client(config['engine_url'],authorization)
        support=Client(config['support_url'],'Bearer '+values['support_token'])
        health={'ready':True,'pid':os.getpid(),'vault_version':config['secret_version'],'last_worker_error':None,'started_utc_s':time.time()}
        if isinstance(key,SigningKeys):
            health.update(active_signing_key_id=key.active,revoked_signing_key_ids=sorted(key.revoked),
                          retired_signing_key_ids=sorted(key.retired))
        api=make_api(registry,values['management_token'],health,config.get('port',8092))
        def checkpoint(event,task):
            if test_checkpoints is None:return
            folder=Path(test_checkpoints);flag=folder/(event+'.arm')
            if not flag.exists():return
            flag.unlink()
            atomic_json(folder/(event+'.reached.json'),{'event':event,'pid':os.getpid(),'task_id':task['id'],'process_id':task['processInstanceId']})
            while not stopping.wait(.25):pass
        def emit(row):print(json.dumps({'event':'bound_worker_result',**row}),flush=True)
        worker=BoundWorker(engine,support,registry,emit,checkpoint)
        def stop(*_):stopping.set()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        thread=threading.Thread(target=api.serve_forever,daemon=True);thread.start()
        print(json.dumps({'event':'verifier_started','pid':os.getpid(),'vault_version':config['secret_version'],'bind':'127.0.0.1','port':api.server_port}),flush=True)
        try:
            while not stopping.is_set():
                try:
                    count=worker.poll();health['last_worker_error']=None;health['ready']=True
                    if not count:stopping.wait(.5)
                except Exception as error:
                    # Preserve ambiguous tasks; their lock expiry permits a later
                    # receipt reconciliation. Never log credentials/payloads.
                    health['last_worker_error']=type(error).__name__;health['ready']=False
                    print(json.dumps({'event':'verifier_poll_error','error_type':type(error).__name__}),flush=True)
                    stopping.wait(1)
        finally:
            api.shutdown();api.server_close();thread.join(timeout=5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--test-checkpoints',type=Path)
    args=parser.parse_args()
    try:serve(json.loads(args.config.read_text()),test_checkpoints=args.test_checkpoints)
    except Exception as error:
        print(json.dumps({'event':'verifier_start_failed','error_type':type(error).__name__}),flush=True)
        raise SystemExit(1)
