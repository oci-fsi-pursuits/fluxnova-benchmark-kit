"""Bounded reset requests serviced over the controller's existing SSH access.

This is a private same-operator coordination queue, not a network service or an
authentication boundary against the owner of the benchmark hosts.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import time
import uuid
from .checkpoint import digest
from .util import safe_run_id


def atomic_new(path, value):
    path=Path(path)
    temporary=path.with_name('.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            temporary.chmod(0o600)
            json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.link(temporary,path)  # Publish complete data, never replace a receipt.
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):
        raise ValueError('Expected SHA-256 identity')
    return value


def validate_receipt(receipt, run_id, expected=None):
    if not isinstance(receipt,dict) or receipt.get('success') is not True or receipt.get('run_id')!=run_id:
        raise ValueError('Reset receipt must report success for this exact run')
    for key in ('dataset_sha256','state_fingerprint'):
        fingerprint(receipt.get(key))
        if expected and receipt[key]!=expected[key]:
            raise ValueError('Reset receipt '+key+' does not match the pinned checkpoint')
    if not isinstance(receipt.get('method'),str) or not receipt['method'].strip():
        raise ValueError('Reset receipt must name the restore method')
    return receipt


class Mailbox:
    def __init__(self,path):
        self.path=Path(path)
        self.meta=json.loads((self.path/'session.json').read_text())
        if self.meta.get('format')!='fluxnova-reset-mailbox-v1':raise ValueError('Unsupported reset mailbox')
        for key in ('dataset_sha256','state_fingerprint'):fingerprint(self.meta[key])

    @classmethod
    def create(cls,path,binding,duration_s,max_resets):
        if not 60<=duration_s<=21600 or not 1<=max_resets<=20:
            raise ValueError('Reset mailbox requires a bounded duration and request limit')
        for key in ('dataset_sha256','state_fingerprint'):fingerprint(binding[key])
        path=Path(path);path.mkdir(mode=0o700,parents=True,exist_ok=False)
        (path/'requests').mkdir(mode=0o700)
        atomic_new(path/'session.json',{'format':'fluxnova-reset-mailbox-v1',
            'session_id':uuid.uuid4().hex,'expires_at':time.time()+duration_s,
            'max_resets':max_resets,**{key:binding[key] for key in ('dataset_sha256','state_fingerprint')}})
        return cls(path)

    def open(self):
        if (self.path/'closed.json').exists() or time.time()>=self.meta['expires_at']:
            raise ValueError('Reset mailbox is closed or expired')

    def submit(self,run_id,timeout_s,expected):
        self.open();safe_run_id(run_id)
        if not 10<=timeout_s<=900:raise ValueError('Reset wait must be 10–900 seconds')
        if time.time()+timeout_s>self.meta['expires_at']:
            raise ValueError('Insufficient mailbox lifetime for reset request')
        for key in ('dataset_sha256','state_fingerprint'):
            if expected[key]!=self.meta[key]:raise ValueError('Mailbox checkpoint identity differs')
        if len(list((self.path/'requests').iterdir()))>=self.meta['max_resets']:
            raise ValueError('Reset request limit reached')
        directory=self.path/'requests'/run_id;directory.mkdir(mode=0o700,exist_ok=False)
        request={'run_id':run_id,'request_id':uuid.uuid4().hex,'session_id':self.meta['session_id'],
                 'created_at':time.time(),'deadline':time.time()+timeout_s,
                 **{key:self.meta[key] for key in ('dataset_sha256','state_fingerprint')}}
        atomic_new(directory/'request.json',request)
        return request

    def validate_request(self,request):
        self.open();safe_run_id(request['run_id'])
        for key in ('session_id','dataset_sha256','state_fingerprint'):
            if request[key]!=self.meta[key]:raise ValueError('Request does not belong to this checkpoint session')
        if not re.fullmatch('[a-f0-9]{32}',request['request_id']):raise ValueError('Invalid request identity')
        now=time.time()
        if not request['created_at']<=now<request['deadline']<=self.meta['expires_at']:
            raise ValueError('Reset request is stale or outside its permitted window')
        if request['deadline']-request['created_at']>901:raise ValueError('Reset request timeout exceeds bound')
        return self.path/'requests'/request['run_id']

    def pending(self):
        self.open();result=[]
        for directory in sorted((self.path/'requests').iterdir()):
            if any((directory/name).exists() for name in ('claimed.json','response.json','abandoned.json')):continue
            if not (directory/'request.json').exists():continue
            request=json.loads((directory/'request.json').read_text())
            if time.time()>=request['deadline']:continue
            if self.validate_request(request)!=directory:raise ValueError('Request path mismatch')
            result.append(request)
        if len(result)>1:raise ValueError('Concurrent reset requests are not permitted')
        return result

    def claim(self,request):
        directory=self.validate_request(request)
        actual=json.loads((directory/'request.json').read_text())
        if actual!=request or (directory/'abandoned.json').exists():raise ValueError('Reset request changed or abandoned')
        atomic_new(directory/'claimed.json',{'request_sha256':digest(request),'claimed_at':time.time()})

    def deliver(self,request,receipt):
        directory=self.path/'requests'/safe_run_id(request['run_id'])
        claimed=json.loads((directory/'claimed.json').read_text())
        if claimed['request_sha256']!=digest(request):raise ValueError('Claim identity mismatch')
        if receipt.get('success') is True:
            validate_receipt(receipt,request['run_id'],self.meta)
            if receipt.get('writers_resumed') is not True:raise ValueError('Benchmark writers did not resume')
        atomic_new(directory/'response.json',{'request_sha256':digest(request),'receipt':receipt})

    def receive(self,request):
        directory=self.path/'requests'/request['run_id']
        if time.time()>=request['deadline']:raise TimeoutError('Reset receipt deadline expired')
        response=directory/'response.json'
        if not response.exists():
            self.open();return None
        value=json.loads(response.read_text())
        if value.get('request_sha256')!=digest(request):raise ValueError('Reset receipt belongs to another request')
        receipt=validate_receipt(value['receipt'],request['run_id'],self.meta)
        if receipt.get('writers_resumed') is not True:raise ValueError('Benchmark writers did not resume')
        return receipt

    def wait(self,run_id,timeout_s,expected):
        # Hold this lock until the client has received the matching receipt.
        active=self.path/'active';active.mkdir(mode=0o700,exist_ok=False)
        request=None
        try:
            request=self.submit(run_id,timeout_s,expected)
            while True:
                receipt=self.receive(request)
                if receipt is not None:return receipt
                time.sleep(1)
        except BaseException:
            if request:atomic_new(self.path/'requests'/run_id/'abandoned.json',{'at':time.time()})
            raise
        finally:active.rmdir()

    def close(self,reason):
        atomic_new(self.path/'closed.json',{'at':time.time(),'reason':reason})
