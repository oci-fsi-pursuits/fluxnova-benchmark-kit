"""Offline monotonic revocation and guarded removal from the current key ring.

These transitions fence the single registry owner. They do not change Vault,
erase historical versions, or make a compromised HMAC authenticate history.
Receipt recovery still trusts the protected registry, engine and ledger.
"""
import hashlib
import json

from services.key_rotation import _migrate_policy
from services.signing_keys import SigningKeys


def require_rings(current, target):
    if not isinstance(current, SigningKeys) or not isinstance(target, SigningKeys):
        raise ValueError('lifecycle_requires_versioned_key_rings')


def revoke_keys(directory, current, target, apply=False):
    """Retain material, disable new sends; an active-key compromise needs a fresh issuer."""
    require_rings(current, target)
    if (target.legacy != current.legacy or dict(target.retired) != dict(current.retired)
            or any(target._keys.get(k) != v for k,v in current._keys.items())
            or not current.revoked < target.revoked):
        raise ValueError('revocation_requires_monotonic_retained_policy')
    added = set(target._keys) - set(current._keys)
    if target.active == current.active:
        if added:raise ValueError('revocation_unrelated_key_change')
    elif (added != {target.active} or target.issuing_key() in current._keys.values()
          or current.active not in target.revoked):
        raise ValueError('revocation_requires_fresh_issuer_for_compromised_active_key')
    return _migrate_policy(directory, current, target, operation='revocation', apply=apply)


def retire_keys(directory, current, target, apply=False):
    """Retire unused revoked material, never material referenced by any intent.

    An expired or settled record still needs its MAC checked at startup/restore.
    Historical copies remain bound to their older retained Vault version.
    """
    require_rings(current, target)
    removed = set(current._keys) - set(target._keys)
    expected_retired = {**current.retired,
                        **{k:hashlib.sha256(current._keys[k]).hexdigest() for k in removed}}
    if (not removed or not removed <= current.revoked or target.active != current.active
            or set(target._keys) != set(current._keys)-removed
            or any(current._keys[k] != v for k,v in target._keys.items())
            or target.revoked != current.revoked-removed
            or dict(target.retired) != expected_retired
            or target.legacy != (None if current.legacy in removed else current.legacy)):
        raise ValueError('retirement_requires_unused_revoked_keys_and_tombstones')

    def require_unreferenced(rows):
        for row in rows['intents']:
            if current.key_id_for(json.loads(row[1])) in removed:
                raise ValueError('retirement_key_still_referenced')

    return _migrate_policy(directory, current, target, operation='retirement', apply=apply,
                           validate_rows=require_unreferenced)
