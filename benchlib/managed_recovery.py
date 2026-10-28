"""Build separate managed PostgreSQL recovery targets and guard their cleanup.

These helpers make no API calls. Retain the source, backup and returned target
IDs in a private receipt; never infer a deletion target from a display name.
"""
import re
from datetime import datetime, timezone


def target_spec(source, backup, run_id):
    if backup['lifecycle_state'] != 'ACTIVE':
        raise ValueError('Backup must be active')
    if backup['db_system_id'] != source['id'] or backup['compartment_id'] != source['compartment_id']:
        raise ValueError('Backup must belong to the exact source and compartment')
    if source['instance_count'] != 1:
        raise ValueError('This backup rehearsal supports one-node sources')
    spec = _base_spec(source, run_id)
    spec['source'] = {'source_type': 'BACKUP', 'backup_id': backup['id']}
    return spec


def engine_failover_spec(source, run_id):
    """Allocate a fresh two-node system for synthetic engine reconnect checks.

    The existing cell supplies allocation/network settings only. No backup,
    source workflow state or source endpoint address is copied into this system.
    Credentials must be supplied separately as a pinned Vault reference.
    """
    if source['instance_count'] != 1:
        raise ValueError('Engine reconnect planning expects the one-node reference cell')
    spec = _base_spec(source, run_id)
    spec.update(instance_count=2, source={'source_type': 'NONE'},
                description='Temporary synthetic engine reconnect test; separate database and ledger')
    spec['freeform_tags']['recovery_lane'] = 'engine-reconnect'
    return spec


def require_owned_engine_failover_target(target, *, source_id, target_id, run_id):
    """Guard promotion/cleanup using a fresh GET and the retained create ID."""
    _require_owned_identity(target, source_id, target_id, run_id)
    if (target.get('source') or {}).get('source_type') != 'NONE':
        raise ValueError('Engine reconnect target must be a fresh database')
    if (target.get('freeform_tags') or {}).get('recovery_lane') != 'engine-reconnect':
        raise ValueError('Target belongs to another recovery lane')
    if target.get('instance_count') != 2:
        raise ValueError('Engine reconnect target must retain its planned two-node allocation')


def _base_spec(source, run_id):
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('A unique 32-character hexadecimal recovery run ID is required')
    if source['lifecycle_state'] != 'ACTIVE' or source['system_type'] != 'OCI_OPTIMIZED_STORAGE':
        raise ValueError('Source must be active with optimized storage')
    network = source['network_details']
    return {
        'display_name': 'fluxnova-recovery-' + run_id[:12],
        'description': 'Temporary managed backup restoration; no application cutover',
        'compartment_id': source['compartment_id'],
        'system_type': source['system_type'],
        'db_version': source['db_version'].split('.')[0],
        'config_id': source['config_id'],
        # GET returns the underlying Compute name; CREATE requires the PSQL
        # namespace even for the same flexible hardware allocation.
        'shape': (source['shape'] if source['shape'].startswith('PostgreSQL.')
                  else 'PostgreSQL.' + source['shape']),
        'instance_count': 1,
        'instance_ocpu_count': source['instance_ocpu_count'],
        'instance_memory_size_in_gbs': source['instance_memory_size_in_gbs'],
        'storage_details': dict(source['storage_details']),
        'network_details': {'subnet_id': network['subnet_id'],
                            'nsg_ids': list(network.get('nsg_ids') or []),
                            'is_reader_endpoint_enabled': False},
        'management_policy': {'backup_policy': {'kind': 'NONE'}, 'pitr_policy': {'kind': 'NONE'}},
        'freeform_tags': {'managed_by': 'fluxnova-benchmarks',
                          'recovery_run_id': run_id, 'temporary_recovery': 'true'},
    }


def require_owned_target(target, *, source_id, backup_id, target_id, run_id):
    """Require a fresh GET matching all retained identities before deletion."""
    _require_owned_identity(target, source_id, target_id, run_id)
    origin = target.get('source') or {}
    if origin.get('source_type') != 'BACKUP' or origin.get('backup_id') != backup_id:
        raise ValueError('Target backup does not match the retained ownership receipt')


def _require_owned_identity(target, source_id, target_id, run_id):
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid recovery run ID')
    tags = target.get('freeform_tags') or {}
    if (not target_id or target_id == source_id or target['id'] != target_id
            or target['display_name'] != 'fluxnova-recovery-' + run_id[:12]
            or tags.get('managed_by') != 'fluxnova-benchmarks'
            or tags.get('temporary_recovery') != 'true'
            or tags.get('recovery_run_id') != run_id):
        raise ValueError('Target does not match the retained recovery ownership receipt')


def _instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Recovery timestamps require an explicit timezone')
    return result.astimezone(timezone.utc)


def pitr_target_spec(source, run_id, restore_at, recovery_windows):
    """Build a new one-node target only for a timestamp in a reported window."""
    stamp = _instant(restore_at)
    if stamp.microsecond:
        raise ValueError('Managed PITR accepts whole-second timestamps')
    if source['instance_count'] not in (1, 2):
        raise ValueError('This PITR rehearsal supports one- or two-node sources')
    if source['management_policy']['pitr_policy']['kind'] != 'STANDARD':
        raise ValueError('Source must have PITR enabled')
    if not any(_instant(w['time_recovery_window_start']) <= stamp <=
               _instant(w['time_recovery_window_end']) for w in recovery_windows):
        raise ValueError('Requested timestamp is outside the reported recovery windows')
    spec = _base_spec(source, run_id)
    spec['description'] = 'Temporary timestamp recovery; no application cutover'
    spec['source'] = {'source_type': 'POINT_IN_TIME_DB_SYSTEM', 'db_system_id': source['id'],
                      'time_to_restore': stamp.isoformat()}
    return spec


def require_owned_pitr_target(target, *, source_id, target_id, run_id, restore_at,
                              creation_spec=None, accepted_target=None):
    _require_owned_identity(target, source_id, target_id, run_id)
    origin = target.get('source') or {}
    if (origin.get('source_type') != 'POINT_IN_TIME_DB_SYSTEM'
            or origin.get('db_system_id') != source_id):
        raise ValueError('Target PITR source/time does not match the retained ownership receipt')
    if origin.get('time_to_restore') is not None:
        if _instant(origin['time_to_restore']) != _instant(restore_at):
            raise ValueError('Reported PITR timestamp does not match the retained receipt')
        return
    # Some service GET responses omit the requested time. Do not invent it in
    # the live object: require the retained pre-create specification AND the
    # original accepted response, bound by the unique name/tag and target ID.
    if not isinstance(creation_spec, dict) or not isinstance(accepted_target, dict):
        raise ValueError('Omitted PITR time requires retained creation and acceptance evidence')
    _require_owned_identity({**creation_spec, 'id': target_id}, source_id, target_id, run_id)
    _require_owned_identity(accepted_target, source_id, target_id, run_id)
    for record in (creation_spec, accepted_target):
        proof = record.get('source') or {}
        if proof.get('source_type') != 'POINT_IN_TIME_DB_SYSTEM' or proof.get('db_system_id') != source_id:
            raise ValueError('PITR creation evidence has a different source')
    if _instant(creation_spec['source'].get('time_to_restore')) != _instant(restore_at):
        raise ValueError('PITR creation specification has a different timestamp')
    accepted_time = accepted_target['source'].get('time_to_restore')
    if accepted_time is not None and _instant(accepted_time) != _instant(restore_at):
        raise ValueError('Accepted PITR timestamp does not match the retained receipt')


def replica_for_failover(db_system, primary_instance_id):
    """Select the other active node using GetPrimaryDbInstance's identity."""
    nodes = db_system['instances']
    ids = [node['id'] for node in nodes]
    if (db_system['lifecycle_state'] != 'ACTIVE' or db_system['instance_count'] != 2
            or len(nodes) != 2 or len(set(ids)) != 2 or primary_instance_id not in ids
            or any(node['lifecycle_state'] != 'ACTIVE' for node in nodes)):
        raise ValueError('Failover requires exactly two active nodes and a matching authoritative primary')
    return next(value for value in ids if value != primary_instance_id)
