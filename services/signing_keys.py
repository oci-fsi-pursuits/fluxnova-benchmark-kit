"""Versioned HMAC intent keys. Key identifiers are authenticated token fields."""
import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from types import MappingProxyType


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


@dataclass(frozen=True, init=False, repr=False)
class SigningKeys:
    """One issuing key, explicitly retained verification keys and a v1 mapping."""
    _keys: object
    active: str
    legacy: object
    revoked: frozenset
    retired: object
    def __init__(self, keys, active, legacy=None, *, revoked=(), retired=None):
        if (not isinstance(keys, dict) or not 1 <= len(keys) <= 16
                or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', k)
                       or not isinstance(v, bytes) or len(v) != 32 for k, v in keys.items())
                or not isinstance(active, str) or active not in keys
                or (legacy is not None and (not isinstance(legacy, str) or legacy not in keys))
                or len(set(keys.values())) != len(keys)):
            raise ValueError('invalid_signing_key_ring')
        if (not isinstance(revoked, (list, tuple, set, frozenset))
                or any(not isinstance(k, str) for k in revoked)
                or len(set(revoked)) != len(revoked)
                or not set(revoked) <= keys.keys() or active in revoked):
            raise ValueError('invalid_revoked_signing_keys')
        retired = {} if retired is None else retired
        if (not isinstance(retired, dict) or len(retired) > 128
                or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', k)
                       or not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v)
                       for k,v in retired.items())
                or set(retired) & keys.keys()
                or len(set(retired.values())) != len(retired)
                or set(retired.values()) & {hashlib.sha256(v).hexdigest() for v in keys.values()}):
            raise ValueError('invalid_retired_signing_keys')
        object.__setattr__(self, '_keys', MappingProxyType(dict(keys)))
        object.__setattr__(self, 'active', active)
        object.__setattr__(self, 'legacy', legacy)
        object.__setattr__(self, 'revoked', frozenset(revoked))
        object.__setattr__(self, 'retired', MappingProxyType(dict(retired)))

    def fingerprint(self):
        policy = {'active':self.active, 'legacy':self.legacy,
                  'keys':{k:hashlib.sha256(v).hexdigest() for k,v in self._keys.items()}}
        prefix = 'keyring-v1:'
        if self.revoked or self.retired:
            prefix = 'keyring-v2:'
            policy.update(revoked=sorted(self.revoked), retired=dict(self.retired))
        return prefix + hashlib.sha256(canonical(policy)).hexdigest()

    def issuing_key(self):
        return self._keys[self.active]

    def key_id_for(self, body):
        if type(body.get('version')) is not int:
            raise ValueError('invalid_intent_version')
        if body['version'] == 1 and 'signing_key_id' not in body:
            key_id = self.legacy
        elif body['version'] == 2:
            key_id = body.get('signing_key_id')
        else:
            raise ValueError('invalid_intent_version')
        if not isinstance(key_id, str) or key_id not in self._keys:
            raise ValueError('untrusted_intent_key')
        return key_id

    def require_authorized(self, body):
        if self.key_id_for(body) in self.revoked:
            raise ValueError('intent_signing_key_revoked')

    def key_for(self, body, *, archival=False):
        key_id = self.key_id_for(body)
        if not archival:self.require_authorized(body)
        return self._keys[key_id]


def require_authority(authority):
    if isinstance(authority, SigningKeys):
        return
    if not isinstance(authority, bytes) or len(authority) < 32:
        raise ValueError('signing_key_too_short')


def authority_fingerprint(authority):
    require_authority(authority)
    return authority.fingerprint() if isinstance(authority, SigningKeys) else hashlib.sha256(authority).hexdigest()


def signing_fields(authority):
    require_authority(authority)
    return {'version':2, 'signing_key_id':authority.active} if isinstance(authority, SigningKeys) else {'version':1}


def token_for_raw(raw, authority, *, archival=False):
    require_authority(authority)
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):raise ValueError('invalid_intent_body')
        if isinstance(authority, SigningKeys):
            key = authority.key_for(body, archival=archival)
        else:
            if type(body.get('version')) is not int or body['version'] != 1 or 'signing_key_id' in body:
                raise ValueError('invalid_intent_version')
            key = authority
    except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError('invalid_intent_body') from None
    return base64.urlsafe_b64encode(raw).decode() + '.' + hmac.new(key, raw, hashlib.sha256).hexdigest()


def authenticate_token(token, authority, *, archival=False):
    if not isinstance(token, str) or len(token) > 4096:
        raise ValueError('invalid_intent_token')
    try:
        encoded, signature = token.split('.')
        raw = base64.b64decode(encoded, altchars=b'-_', validate=True)
    except (ValueError, UnicodeError):
        raise ValueError('invalid_intent_token') from None
    if not re.fullmatch('[0-9a-f]{64}', signature):
        raise ValueError('intent_signature_mismatch')
    expected = token_for_raw(raw, authority, archival=archival).rsplit('.', 1)[1]
    if not hmac.compare_digest(signature, expected):
        raise ValueError('intent_signature_mismatch')
    return raw


def authenticate_registered(body, token, authority):
    # Registry/recovery integrity is distinct from permission to send. A
    # revoked key remains available only to check these retained exact bytes.
    raw = authenticate_token(token, authority, archival=True)
    if (raw != body.encode() or raw != canonical(json.loads(body))
            or not hmac.compare_digest(token, token_for_raw(raw, authority, archival=True))):
        raise ValueError('intent_registry_mismatch')
