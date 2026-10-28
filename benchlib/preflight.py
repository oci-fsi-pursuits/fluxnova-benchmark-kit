import math
import re
import shutil
from urllib.parse import urlparse
from .contracts import validate_config

def check(config, cloud=True, need_endpoint=False):
    errors = list(validate_config(config, require_cloud=cloud, require_built_image=need_endpoint))
    if errors:
        return sorted(set(errors))
    workload = config.get("workload", {})
    for field in ("rate", "duration_s", "warmup_s", "drain_s", "preallocated_vus", "max_vus"):
        value = workload.get(field, 256 if field.endswith("vus") else 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < (0 if field == "warmup_s" else 1):
            errors.append(f"workload.{field} must be an integer supported by the k6 profile")
    if errors:
        return sorted(set(errors))
    if workload.get("max_vus", 256) < workload.get("preallocated_vus", 256):
        errors.append("max_vus cannot be smaller than preallocated_vus")
    for field in ("duration_s", "rate", "deadline_s"):
        value = workload.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            errors.append(f"workload.{field} must be finite and positive")
    if workload.get("drain_s", 300) < workload.get("deadline_s", 1):
        errors.append("drain_s must cover deadline_s for complete deadline accounting")
    execution = config.get("execution", {})
    total = sum(workload.get(k, 0) for k in ("duration_s", "warmup_s", "drain_s"))
    if execution.get("max_run_seconds", total + 120) < total:
        errors.append("max_run_seconds must cover warmup + measurement + drain")
    if cloud:
        c = config.get("campaign", {})
        cost = config.get("estimated_cost", {})
        amount, limit = cost.get("amount"), c.get("budget_limit")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount <= 0:
            errors.append("estimated_cost.amount must be a positive campaign estimate")
        elif isinstance(limit, (int, float)) and amount > limit:
            errors.append("estimated campaign cost exceeds budget_limit")
        if cost.get("currency") != c.get("currency"):
            errors.append("cost estimate and budget currencies must match")
        if not cost.get("basis"):
            errors.append("estimated_cost.basis is required (dated prices and allocation assumptions)")
        for key in ("availability_domain", "image_ocid", "ssh_authorized_key", "db_admin_secret_id", "db_shape", "artifact_bucket_name"):
            if not config.get("oci", {}).get(key):
                errors.append(f"oci.{key} is required")
        for key in ("db_ocpu_count", "db_memory_gb", "db_storage_iops"):
            if not isinstance(config.get("oci", {}).get(key), (int, float)) or config["oci"][key] <= 0:
                errors.append(f"oci.{key} must be resolved from regional service availability")
        if not re.fullmatch(r"[0-9a-f]{40}", config.get("artifact", {}).get("source_commit", "")):
            errors.append("artifact.source_commit must be pinned to a full commit SHA")
    if need_endpoint:
        r = config.get("runtime", {})
        endpoint = urlparse(r.get("base_url", ""))
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
            errors.append("runtime.base_url is required")
        if cloud and endpoint.scheme != "https" and not r.get("allow_insecure_http", False):
            errors.append("cloud runtime requires HTTPS, or explicit allow_insecure_http for an isolated lab")
    return sorted(set(errors))
