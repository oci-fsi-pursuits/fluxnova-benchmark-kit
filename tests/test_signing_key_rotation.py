import base64
from contextlib import closing
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchlib.recovery import registry_check
from scripts.rotate_verifier_keys import run, validate_configs
from services.bound_payment import BoundWorker, IntentRegistry, payment_request
from services.key_rotation import logical_rows, rotate_keys
from services.mock_service import Ledger
from services.payment_approval import snapshot, typed
from services.recoverable_agent import digest, owner
from services.signing_keys import SigningKeys, authenticate_token, canonical
from services.verifier_service import decode_secret, load_vault

PAYMENT={'amount_minor':2000,'limit_minor':1000,'authorized':True,'sanctions_match':False}
OLD=b'a'*32;NEW=b'b'*32;THIRD=b'c'*32


class SigningKeyRotationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.path=self.root/'intents.sqlite';self.old=IntentRegistry(self.path,OLD)
        self.intent=self.old.issue(PAYMENT,'reviewer',request_id='original-request')
        self.old.bind(self.intent['key'],'process','screen','screen-key')
        self.ring=SigningKeys({'initial':OLD,'next':NEW},'next','initial')
        self.values={**snapshot(PAYMENT,self.intent['key']),'paymentBeneficiary':'synthetic-beneficiary',
            'boundIntent':self.intent['token'],'reviewedPaymentSha256':digest(PAYMENT),'reviewerDecision':'approve'}
        self.task={'id':'external','businessKey':self.intent['key'],'processInstanceId':'process','variables':typed(self.values)}

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:return logical_rows(db)

    def test_explicit_rotation_preserves_tokens_bindings_and_old_issuance_identity(self):
        before=self.rows();receipt=rotate_keys(self.root,OLD,self.ring,apply=True)
        self.assertTrue(receipt['applied']);self.assertEqual(self.rows(),before)
        registry=IntentRegistry(self.path,self.ring)
        self.assertEqual(registry.verify(self.task,self.values)['process_id'],'process')
        self.assertEqual(registry.issue(PAYMENT,'reviewer',request_id='original-request'),self.intent)
        with self.assertRaisesRegex(ValueError,'issuance_request_conflict'):
            registry.issue({**PAYMENT,'amount_minor':3000},'reviewer',request_id='original-request')
        new=registry.issue(PAYMENT,'reviewer',request_id='new-request')
        body=json.loads(authenticate_token(new['token'],self.ring))
        self.assertEqual(body['version'],2);self.assertEqual(body['signing_key_id'],'next')
        with self.assertRaises(ValueError):authenticate_token(new['token'],OLD)
        restored=IntentRegistry(self.path,self.ring)
        self.assertEqual(restored.issue(PAYMENT,'reviewer',request_id='new-request'),new)
        backup=self.root/receipt['backup_file']
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(),receipt['backup_sha256'])
        self.assertEqual(registry_check(backup,OLD)['intents'],1)
        self.assertEqual(registry_check(self.path,self.ring)['intents'],2)

    def test_keyring_cannot_silently_adopt_or_replace_legacy_registry(self):
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'key_mismatch'):IntentRegistry(self.path,self.ring)
        self.assertEqual(self.path.read_bytes(),before)

    def test_old_constructor_and_existing_issuer_bind_and_verify_are_fenced(self):
        rotate_keys(self.root,OLD,self.ring,apply=True)
        for action in [lambda:IntentRegistry(self.path,OLD),lambda:self.old.issue(PAYMENT,'reviewer'),
                       lambda:self.old.bind(self.intent['key'],'process','screen','screen-key'),
                       lambda:self.old.verify(self.task,self.values)]:
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'key_mismatch'):action()

    def test_second_rotation_preserves_both_generations_and_fences_stale_ring(self):
        rotate_keys(self.root,OLD,self.ring,apply=True);stale=IntentRegistry(self.path,self.ring)
        second=stale.issue(PAYMENT,'reviewer',request_id='second')
        third=SigningKeys({'initial':OLD,'next':NEW,'third':THIRD},'third','initial')
        rotate_keys(self.root,self.ring,third,apply=True);current=IntentRegistry(self.path,third)
        self.assertEqual(current.issue(PAYMENT,'reviewer',request_id='second'),second)
        self.assertEqual(current.verify(self.task,self.values)['process_id'],'process')
        with self.assertRaisesRegex(ValueError,'key_mismatch'):stale.issue(PAYMENT,'reviewer')

    def test_unknown_relabelled_and_legacy_key_cannot_authenticate_new_tokens(self):
        rotate_keys(self.root,OLD,self.ring,apply=True);registry=IntentRegistry(self.path,self.ring)
        intent=registry.issue(PAYMENT,'reviewer');raw=authenticate_token(intent['token'],self.ring)
        body=json.loads(raw);signature=intent['token'].rsplit('.',1)[1]
        for kid in ['unknown','initial']:
            altered=canonical({**body,'signing_key_id':kid})
            token=base64.urlsafe_b64encode(altered).decode()+'.'+signature
            with self.subTest(kid=kid),self.assertRaises(ValueError):authenticate_token(token,self.ring)
        changed=base64.urlsafe_b64encode(raw).decode()+'.'+'0'*64
        with self.assertRaisesRegex(ValueError,'signature'):authenticate_token(changed,self.ring)

    def test_expired_old_receipt_reconciles_once_but_empty_ledger_cannot_send(self):
        ledger=Ledger(self.root/'ledger.sqlite');self.assertEqual(ledger.execute(payment_request(self.intent))[0],200)
        rotate_keys(self.root,OLD,self.ring,apply=True);registry=IntentRegistry(self.path,self.ring)
        expires=registry.load(self.intent['key'])['body']['expires_utc_s']
        for committed in [True,False]:
            support=Mock();support.request.side_effect=lambda method,path,body=None: (
                ledger.state(self.intent['key']) if committed else {'result':None,'effect_count':0,'violations':[]})
            engine=Mock();worker=BoundWorker(engine,support,registry)
            with patch('services.bound_payment.verify_history'),patch('services.bound_payment.time.time',return_value=expires+1):
                worker.process(self.task)
            self.assertFalse(any(c.args[0]=='POST' for c in support.request.call_args_list))
            self.assertEqual(engine.request.call_args.args[2]['variables']['workerResult']['value'],
                             'settled' if committed else 'integrity_rejected')
        self.assertEqual(ledger.state(self.intent['key'])['effect_count'],1)

    def test_active_owner_blocks_rotation_before_backup_or_metadata_change(self):
        before=self.path.read_bytes()
        with owner(self.root),self.assertRaises(OSError):rotate_keys(self.root,OLD,self.ring,apply=True)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(list(self.root.glob('intents.before-*')))

    def test_dry_run_preserves_registry_and_creates_no_backup(self):
        before=self.path.read_bytes();receipt=rotate_keys(self.root,OLD,self.ring)
        self.assertFalse(receipt['applied']);self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(list(self.root.glob('intents.before-*')))

    def test_backup_sync_failure_keeps_original_policy_and_recovery_copy(self):
        before=self.path.read_bytes()
        with patch('services.key_rotation.os.fsync',side_effect=OSError('injected_sync_failure')):
            with self.assertRaises(OSError):rotate_keys(self.root,OLD,self.ring,apply=True)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(len(list(self.root.glob('intents.before-*'))),1)
        self.assertEqual(IntentRegistry(self.path,OLD).load(self.intent['key'])['token'],self.intent['token'])

    def test_postcommit_sync_error_never_silently_rolls_back_committed_rotation(self):
        before=self.rows()
        with patch('services.key_rotation.os.fsync',side_effect=[None,OSError('injected_after_commit')]):
            with self.assertRaises(OSError):rotate_keys(self.root,OLD,self.ring,apply=True)
        self.assertEqual(self.rows(),before)
        self.assertEqual(IntentRegistry(self.path,self.ring).load(self.intent['key'])['token'],self.intent['token'])
        with self.assertRaisesRegex(ValueError,'key_mismatch'):IntentRegistry(self.path,OLD)
        self.assertEqual(len(list(self.root.glob('intents.before-*'))),1)

    def test_missing_replaced_or_reused_keys_cannot_rotate(self):
        invalid=[SigningKeys({'initial':NEW,'next':THIRD},'next','initial'),
                 SigningKeys({'next':NEW},'next'),SigningKeys({'initial':OLD,'next':NEW},'initial','initial')]
        before=self.path.read_bytes()
        for ring in invalid:
            with self.subTest(ring=ring),self.assertRaises(ValueError):rotate_keys(self.root,OLD,ring,apply=True)
            self.assertEqual(self.path.read_bytes(),before)
        rotate_keys(self.root,OLD,self.ring,apply=True)
        with self.assertRaisesRegex(ValueError,'retained_verification'):
            rotate_keys(self.root,self.ring,SigningKeys({'next':NEW,'third':THIRD},'third'),apply=True)
        with self.assertRaisesRegex(ValueError,'key_mismatch'):
            IntentRegistry(self.path,SigningKeys({'next':NEW},'next'))

    def test_tampered_registered_body_or_token_blocks_rotation(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE intents SET token='corrupt'");db.commit()
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):rotate_keys(self.root,OLD,self.ring,apply=True)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(list(self.root.glob('intents.before-*')))

    def test_key_ring_rejects_aliases_and_cannot_be_mutated(self):
        for keys,active,legacy in [({'x':OLD,'y':OLD},'x',None),({'x':b'short'},'x',None),
                                   ({'bad key':OLD},'bad key',None),({'x':OLD},'missing',None)]:
            with self.assertRaises(ValueError):SigningKeys(keys,active,legacy)
        with self.assertRaises(FrozenInstanceError):self.ring.active='initial'
        with self.assertRaises(TypeError):self.ring._keys['initial']=NEW


class VaultRotationConfigurationTests(unittest.TestCase):
    def values(self):
        return {'version':1,'signing_key_b64':base64.b64encode(OLD).decode(),
                'management_token':'m'*32,'engine_username':'verifier','engine_password':'private','support_token':'private'}

    def test_legacy_and_keyring_secret_decoding_and_malformed_bundles(self):
        values=self.values();self.assertEqual(decode_secret(values)[1],OLD)
        v2={k:v for k,v in values.items() if k!='signing_key_b64'}
        v2.update(version=2,signing_keys_b64={'old':base64.b64encode(OLD).decode(),'new':base64.b64encode(NEW).decode()},
                  active_signing_key_id='new',legacy_signing_key_id='old')
        self.assertEqual(decode_secret(v2)[1].active,'new')
        for changed in [{**v2,'active_signing_key_id':'missing'},{**v2,'legacy_signing_key_id':'missing'},
                        {**v2,'signing_keys_b64':{'old':'%%%'}},{**values,'version':True},
                        {**v2,'signing_key_b64':values['signing_key_b64']}]:
            with self.subTest(changed=list(changed)),self.assertRaises(ValueError):decode_secret(changed)

    def test_exact_vault_version_is_pinned_and_response_mismatch_rejected(self):
        sdk=Mock();bundle=sdk.secrets.SecretsClient.return_value.get_secret_bundle.return_value.data
        bundle.version_number=2;bundle.secret_bundle_content.content=base64.b64encode(json.dumps(self.values()).encode()).decode()
        with patch.dict('sys.modules',{'oci':sdk}):
            self.assertEqual(load_vault({'region':'region','secret_id':'id','secret_version':2})[1],OLD)
            self.assertEqual(sdk.secrets.SecretsClient.return_value.get_secret_bundle.call_args.kwargs['version_number'],2)
            bundle.version_number=3
            with self.assertRaisesRegex(ValueError,'version_mismatch'):
                load_vault({'region':'region','secret_id':'id','secret_version':2})
            bundle.version_number=True
            with self.assertRaisesRegex(ValueError,'version_mismatch'):
                load_vault({'region':'region','secret_id':'id','secret_version':1})

    def test_rotation_cli_rejects_endpoint_identity_and_unrelated_credential_changes(self):
        config={'region':'region','secret_id':'id','secret_version':1,'engine_url':'engine',
                'support_url':'support','state_directory':'state','port':8092}
        target={**config,'secret_version':2};validate_configs(config,target)
        for changed in [{**target,'engine_url':'other'},{**target,'secret_version':1},
                        {**target,'secret_id':'other'},{**target,'state_directory':'other'}]:
            with self.assertRaises(ValueError):validate_configs(config,changed)
        values=self.values();loader=Mock(side_effect=[(values,OLD),({**values,'management_token':'different'},OLD)])
        with self.assertRaisesRegex(ValueError,'preserve_service_credentials'):run(config,target,loader=loader)


if __name__=='__main__':unittest.main()
