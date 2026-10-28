import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchlib.recovery import registry_check
from scripts.change_verifier_key_policy import run
from services.bound_payment import BoundWorker, IntentRegistry, payment_request
from services.key_lifecycle import revoke_keys, retire_keys
from services.key_rotation import logical_rows, rotate_keys
from services.mock_service import Ledger
from services.payment_approval import snapshot, typed
from services.recoverable_agent import digest, owner
from services.signing_keys import SigningKeys, authenticate_token, canonical, token_for_raw
from services.verifier_service import decode_secret

OLD=b'a'*32;NEW=b'b'*32;THIRD=b'c'*32
PAYMENT={'amount_minor':2000,'limit_minor':1000,'authorized':True,'sanctions_match':False}


class KeyLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.path=self.root/'intents.sqlite';old=IntentRegistry(self.path,OLD)
        self.intent=old.issue(PAYMENT,'reviewer',request_id='old-request')
        old.bind(self.intent['key'],'process','screen','screen-key')
        self.unbound=old.issue(PAYMENT,'reviewer',request_id='unbound-request')
        self.current=SigningKeys({'old':OLD,'new':NEW},'new','old')
        rotate_keys(self.root,OLD,self.current,apply=True)
        self.stale=IntentRegistry(self.path,self.current)
        self.target=SigningKeys({'old':OLD,'new':NEW},'new','old',revoked=['old'])
        self.values={**snapshot(PAYMENT,self.intent['key']),'paymentBeneficiary':'synthetic-beneficiary',
                     'boundIntent':self.intent['token'],'reviewedPaymentSha256':digest(PAYMENT),'reviewerDecision':'approve'}
        self.task={'id':'external','businessKey':self.intent['key'],'processInstanceId':'process','variables':typed(self.values)}

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:return logical_rows(db)

    def apply(self):
        receipt=revoke_keys(self.root,self.current,self.target,apply=True)
        return IntentRegistry(self.path,self.target),receipt

    def test_revocation_preserves_exact_rows_old_request_identity_and_new_issuance(self):
        before=self.rows();registry,receipt=self.apply()
        self.assertEqual(self.rows(),before);self.assertTrue(receipt['applied'])
        self.assertEqual(receipt['revoked_signing_key_ids'],['old'])
        self.assertEqual(registry.issue(PAYMENT,'reviewer',request_id='old-request'),self.intent)
        new=registry.issue(PAYMENT,'reviewer',request_id='new-request')
        self.assertEqual(json.loads(authenticate_token(new['token'],self.target))['signing_key_id'],'new')
        reopened=IntentRegistry(self.path,self.target)
        self.assertEqual(reopened.issue(PAYMENT,'reviewer',request_id='new-request'),new)
        self.assertEqual(reopened.issue(PAYMENT,'reviewer',request_id='old-request'),self.intent)
        backup=self.root/receipt['backup_file']
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),receipt['backup_sha256'])
        self.assertEqual(registry_check(backup,self.current)['intents'],2)
        self.assertEqual(registry_check(self.path,self.target)['intents'],3)

    def test_authorization_rejects_revoked_key_but_archival_verification_does_not(self):
        registry,_=self.apply()
        with self.assertRaisesRegex(ValueError,'key_revoked'):authenticate_token(self.intent['token'],self.target)
        with self.assertRaisesRegex(ValueError,'key_revoked'):registry.verify(self.task,self.values,allow_expired=True)
        record=registry.verify(self.task,self.values,allow_expired=True,receipt_only=True)
        self.assertEqual(record['process_id'],'process')
        with self.assertRaisesRegex(ValueError,'key_revoked'):registry.require_send_authority(record['body'])

    def test_revoked_pending_cannot_send_and_committed_receipt_reconciles_without_post(self):
        registry,_=self.apply();expires=registry.load(self.intent['key'])['body']['expires_utc_s']
        for committed in [False,True]:
            for expired in [False,True]:
                with self.subTest(committed=committed,expired=expired):
                    ledger=Ledger(self.root/f'ledger-{committed}-{expired}.sqlite')
                    if committed:self.assertEqual(ledger.execute(payment_request(self.intent))[0],200)
                    before=ledger.state(self.intent['key']);support=Mock()
                    support.request.side_effect=lambda method,path,body=None:ledger.state(path.rsplit('/',1)[1])
                    engine=Mock();worker=BoundWorker(engine,support,registry)
                    with patch('services.bound_payment.verify_history'),patch('services.bound_payment.time.time',return_value=expires+(1 if expired else -1)):
                        worker.process(self.task);worker.process(self.task)
                    self.assertFalse(any(c.args[0]=='POST' for c in support.request.call_args_list))
                    self.assertEqual(ledger.state(self.intent['key']),before)
                    variables=engine.request.call_args.args[2]['variables']
                    self.assertEqual(variables['workerResult']['value'],'settled' if committed else 'integrity_rejected')
                    if not committed:self.assertEqual(variables['integrityReason']['value'],'intent_signing_key_revoked')

    def test_compromised_key_forgery_and_process_swap_cannot_use_archival_path(self):
        registry,_=self.apply();body=registry.load(self.intent['key'])['body']
        forged=token_for_raw(canonical({**body,'reviewer':'attacker'}),self.current)
        for task,values in [(self.task,{**self.values,'boundIntent':forged}),
                            ({**self.task,'processInstanceId':'another'},self.values),
                            (self.task,{**self.values,'paymentAmountMinor':3000})]:
            engine,support=Mock(),Mock();worker=BoundWorker(engine,support,registry)
            worker.process({**task,'variables':typed(values)})
            support.request.assert_not_called()
            self.assertEqual(engine.request.call_args.args[2]['variables']['workerResult']['value'],'integrity_rejected')

    def test_revoked_key_cannot_create_new_binding_but_exact_binding_replay_is_preserved(self):
        registry,_=self.apply();registry.bind(self.intent['key'],'process','screen','screen-key')
        with self.assertRaisesRegex(ValueError,'key_revoked'):
            registry.bind(self.unbound['key'],'new-process','new-screen','new-screen-key')
        self.assertIsNone(registry.load(self.unbound['key'])['process_id'])

    def test_stale_registry_and_policy_rollback_are_fenced(self):
        self.apply()
        for call in [lambda:IntentRegistry(self.path,self.current),lambda:self.stale.issue(PAYMENT,'reviewer'),
                     lambda:self.stale.bind(self.intent['key'],'process','screen','screen-key'),
                     lambda:self.stale.verify(self.task,self.values,receipt_only=True)]:
            with self.assertRaisesRegex(ValueError,'key_mismatch'):call()
        undone=SigningKeys({'old':OLD,'new':NEW,'third':THIRD},'third','old')
        with self.assertRaisesRegex(ValueError,'lifecycle_policy'):
            rotate_keys(self.root,self.target,undone,apply=True)
        with self.assertRaisesRegex(ValueError,'monotonic'):
            revoke_keys(self.root,self.target,self.current,apply=True)

    def test_metadata_change_at_pre_send_checkpoint_prevents_ledger_post(self):
        registry=IntentRegistry(self.path,self.current);engine,support=Mock(),Mock()
        support.request.return_value={'result':None,'effect_count':0,'violations':[]}
        def checkpoint(event,task):revoke_keys(self.root,self.current,self.target,apply=True)
        with patch('services.bound_payment.verify_history'),self.assertRaisesRegex(ValueError,'key_mismatch'):
            BoundWorker(engine,support,registry,checkpoint=checkpoint).process(self.task)
        self.assertFalse(any(c.args[0]=='POST' for c in support.request.call_args_list))
        engine.request.assert_not_called()

    def test_active_compromise_requires_new_material_and_old_active_revocation(self):
        emergency=SigningKeys({'old':OLD,'new':NEW,'third':THIRD},'third','old',revoked=['new'])
        receipt=revoke_keys(self.root,self.current,emergency,apply=True)
        self.assertTrue(receipt['applied']);registry=IntentRegistry(self.path,emergency)
        new=registry.issue(PAYMENT,'reviewer')
        self.assertEqual(json.loads(authenticate_token(new['token'],emergency))['signing_key_id'],'third')
        invalid=SigningKeys({'old':OLD,'new':NEW},'old','old',revoked=['new'])
        with self.assertRaisesRegex(ValueError,'fresh_issuer'):
            revoke_keys(self.root,self.current,invalid)

    def test_dry_run_and_owner_lock_preserve_registry_and_do_not_create_backup(self):
        before=self.path.read_bytes();backups=set(self.root.glob('intents.before-*'))
        self.assertFalse(revoke_keys(self.root,self.current,self.target)['applied'])
        self.assertEqual(self.path.read_bytes(),before)
        with owner(self.root),self.assertRaises(OSError):self.apply()
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(set(self.root.glob('intents.before-*')),backups)

    def test_sync_failure_preserves_backup_and_never_rolls_back_ambiguous_commit(self):
        with patch('services.key_rotation.os.fsync',side_effect=[None,OSError('after_commit')]):
            with self.assertRaises(OSError):self.apply()
        self.assertEqual(registry_check(self.path,self.target)['intents'],2)
        with self.assertRaisesRegex(ValueError,'key_mismatch'):IntentRegistry(self.path,self.current)
        self.assertEqual(len(list(self.root.glob('intents.before-key-revocation-*'))),1)

    def test_prerevocation_backup_requires_offline_policy_migration_before_activation(self):
        _,receipt=self.apply();restored=self.root/'restored';restored.mkdir()
        (restored/'intents.sqlite').write_bytes((self.root/receipt['backup_file']).read_bytes())
        with self.assertRaisesRegex(ValueError,'key_mismatch'):
            IntentRegistry(restored/'intents.sqlite',self.target)
        revoke_keys(restored,self.current,self.target,apply=True)
        registry=IntentRegistry(restored/'intents.sqlite',self.target)
        self.assertEqual(registry.issue(PAYMENT,'reviewer',request_id='old-request'),self.intent)
        with self.assertRaisesRegex(ValueError,'key_revoked'):registry.verify(self.task,self.values)
        self.assertEqual(registry_check(restored/'intents.sqlite',self.target)['intents'],2)

    def test_referenced_key_cannot_retire_even_after_expiry_or_ledger_commit(self):
        registry,_=self.apply();before=self.path.read_bytes()
        ledger=Ledger(self.root/'committed.sqlite');ledger.execute(payment_request(self.intent))
        target=SigningKeys({'new':NEW},'new',retired={'old':hashlib.sha256(OLD).hexdigest()})
        with patch('services.bound_payment.time.time',return_value=10**20):
            with self.assertRaisesRegex(ValueError,'still_referenced'):
                retire_keys(self.root,self.target,target,apply=True)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(list(self.root.glob('intents.before-key-retirement-*')))
        self.assertEqual(registry_check(self.path,self.target)['intents'],2)

    def test_archival_load_detects_new_tampering_after_startup(self):
        registry,_=self.apply()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE intents SET token='changed' WHERE key=?",(self.intent['key'],));db.commit()
        with self.assertRaises(ValueError):registry.load(self.intent['key'])


class RetirementAndBundleTests(unittest.TestCase):
    def test_unused_revoked_key_retirement_restores_and_tombstones_prevent_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'intents.sqlite'
            initial=SigningKeys({'old':OLD,'new':NEW,'unused':THIRD},'new','old')
            registry=IntentRegistry(path,initial);intent=registry.issue(PAYMENT,'reviewer',request_id='original')
            revoked=SigningKeys(dict(initial._keys),'new','old',revoked=['unused'])
            revoke_keys(root,initial,revoked,apply=True)
            retired=SigningKeys({'old':OLD,'new':NEW},'new','old',retired={'unused':hashlib.sha256(THIRD).hexdigest()})
            before=path.read_bytes();self.assertFalse(retire_keys(root,revoked,retired)['applied'])
            self.assertEqual(path.read_bytes(),before)
            with owner(root),self.assertRaises(OSError):retire_keys(root,revoked,retired,apply=True)
            receipt=retire_keys(root,revoked,retired,apply=True)
            self.assertEqual(registry_check(path,retired)['intents'],1)
            self.assertEqual(IntentRegistry(path,retired).issue(PAYMENT,'reviewer',request_id='original'),intent)
            self.assertEqual(registry_check(root/receipt['backup_file'],revoked)['intents'],1)
            for keys in [{'old':OLD,'new':NEW,'unused':b'd'*32}, {'old':OLD,'new':NEW,'alias':THIRD}]:
                with self.assertRaisesRegex(ValueError,'retired_signing'):
                    SigningKeys(keys,'new','old',retired=dict(retired.retired))
            with self.assertRaisesRegex(ValueError,'lifecycle_policy'):
                rotate_keys(root,retired,SigningKeys({'old':OLD,'new':NEW,'again':THIRD},'again','old'))

    def test_retirement_cannot_drop_nonrevoked_key_or_omit_tombstone(self):
        current=SigningKeys({'old':OLD,'new':NEW},'new','old')
        for source,target in [(current,SigningKeys({'new':NEW},'new',retired={'old':hashlib.sha256(OLD).hexdigest()})),
                              (SigningKeys(dict(current._keys),'new','old',revoked=['old']),SigningKeys({'new':NEW},'new'))]:
            with self.assertRaisesRegex(ValueError,'unused_revoked'):
                retire_keys('unused',source,target)

    def test_v3_decoding_policy_integrity_and_legacy_fingerprint_compatibility(self):
        v2={'version':2,'signing_keys_b64':{'old':base64.b64encode(OLD).decode(),'new':base64.b64encode(NEW).decode()},
            'active_signing_key_id':'new','legacy_signing_key_id':'old','management_token':'m'*32,
            'engine_username':'verifier','engine_password':'private','support_token':'private'}
        ring=SigningKeys({'old':OLD,'new':NEW},'new','old')
        expected={'active':'new','legacy':'old','keys':{'old':hashlib.sha256(OLD).hexdigest(),'new':hashlib.sha256(NEW).hexdigest()}}
        self.assertEqual(ring.fingerprint(),'keyring-v1:'+hashlib.sha256(canonical(expected)).hexdigest())
        self.assertEqual(decode_secret(v2)[1].fingerprint(),ring.fingerprint())
        v3={**v2,'version':3,'revoked_signing_key_ids':['old'],'retired_signing_keys_sha256':{}}
        revoked=decode_secret(v3)[1];self.assertNotEqual(revoked.fingerprint(),ring.fingerprint())
        for changed in [{**v3,'revoked_signing_key_ids':['new']},{**v3,'revoked_signing_key_ids':['missing']},
                        {**v3,'revoked_signing_key_ids':['old','old']},{**v3,'revoked_signing_key_ids':'old'},
                        {**v3,'retired_signing_keys_sha256':{'alias':hashlib.sha256(OLD).hexdigest()}},
                        {**v3,'retired_signing_keys_sha256':{'x':'invalid'}},
                        {**v3,'retired_signing_keys_sha256':None},
                        {**v2,'revoked_signing_key_ids':['old']}]:
            with self.subTest(changed=changed.keys()),self.assertRaises(ValueError):decode_secret(changed)

    def test_cli_rejects_endpoint_version_and_service_credential_changes(self):
        config={'region':'fixture','secret_id':'id','secret_version':2,'engine_url':'engine',
                'support_url':'support','state_directory':'state','port':8092}
        target={**config,'secret_version':3};values={k:'unchanged' for k in ['management_token','engine_username','engine_password','support_token']}
        ring=SigningKeys({'old':OLD,'new':NEW},'new','old')
        revoked=SigningKeys(dict(ring._keys),'new','old',revoked=['old'])
        for changed in [{**target,'engine_url':'other'},{**target,'secret_version':2},{**target,'secret_id':'other'}]:
            loader=Mock()
            with self.assertRaises(ValueError):run(config,changed,'revoke',loader=loader)
            loader.assert_not_called()
        loader=Mock(side_effect=[(values,ring),({**values,'management_token':'other'},revoked)])
        with self.assertRaisesRegex(ValueError,'preserve_service_credentials'):run(config,target,'revoke',loader=loader)


if __name__=='__main__':unittest.main()
