"""Allocation and identity guards for the isolated FNV-06 qualification cell."""
from datetime import datetime, timezone
from pathlib import PurePosixPath
import re

from benchlib.managed_recovery import engine_failover_spec


def database_spec(source, run_id):
    spec = engine_failover_spec(source, run_id)
    spec.update(instance_count=1, display_name='fluxnova-fnv06-' + run_id[:12],
                description='Temporary isolated FNV-06 functional qualification')
    spec['freeform_tags'] = {'managed_by': 'fluxnova-benchmarks', 'fnv06_run_id': run_id,
                             'temporary_comparison': 'true', 'comparison_lane': 'fnv06',
                             'campaign_budget_usd': '400'}
    return spec


def require_owned_database(target, *, source_id, target_id, run_id):
    expected = {'managed_by': 'fluxnova-benchmarks', 'fnv06_run_id': run_id,
                'temporary_comparison': 'true', 'comparison_lane': 'fnv06'}
    if (not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id)
            or not source_id or not target_id or target_id == source_id
            or target.get('id') != target_id or target.get('display_name') != 'fluxnova-fnv06-' + run_id[:12]
            or any((target.get('freeform_tags') or {}).get(k) != v for k, v in expected.items())
            or target.get('instance_count') != 1
            or (target.get('source') or {}).get('source_type') != 'NONE'):
        raise ValueError('Target does not match the retained FNV-06 allocation identity')


def require_stage(meta, stage, *, now=None, require_time=True):
    run_id = meta.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id):
        raise ValueError('Invalid FNV-06 run ID')
    expected = PurePosixPath('/opt/fluxnova-bench/workspace') / ('fnv06-' + run_id)
    host = meta.get('connection', {}).get('primary_db_endpoint', {}).get('fqdn', '')
    if (meta.get('study_id') != 'FNV-06' or PurePosixPath(str(stage)) != expected
            or meta.get('stage') != str(expected) or not meta.get('source_id')
            or not meta.get('target_id') or meta['source_id'] == meta['target_id']
            or host == meta.get('source_db_host')
            or not re.fullmatch(r'[a-z0-9.-]+\.postgresql\.us-phoenix-1\.oci\.oraclecloud\.com', host)
            or not meta.get('connection', {}).get('ca_certificate')
            or set(meta.get('images', {})) != {'engine', 'services'}
            or any(not re.fullmatch(r'[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}', value)
                   for value in meta['images'].values())
            or meta.get('budget_limit_usd') != 400):
        raise ValueError('Invalid or unowned FNV-06 stage')
    stop = datetime.fromisoformat(meta['stop_before_utc'])
    prepared = datetime.fromisoformat(meta['prepared_at'])
    if not stop.tzinfo or not prepared.tzinfo or not 0 < (stop - prepared).total_seconds() <= 21600:
        raise ValueError('FNV-06 requires an explicit qualification window of at most six hours')
    if require_time and not prepared <= (now or datetime.now(timezone.utc)) < stop:
        raise ValueError('FNV-06 qualification is outside its declared window')
    return run_id
