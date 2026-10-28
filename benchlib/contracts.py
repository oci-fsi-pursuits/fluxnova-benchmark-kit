"""Small, dependency-free validators for the benchmark evidence contract.

The JSON schemas in schemas/ document the interchange format. This module is
the executable validator, including semantic rules not expressible in schema.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from urllib.parse import urlsplit

SCHEMA_VERSION = "1.0"
EVENT_TYPES = {"scheduled", "started", "accepted", "completed", "dropped", "error", "outcome"}
OUTCOME_STATUSES = {"correct", "incorrect", "unknown", "expected_rejection"}
SCENARIOS = {"minimal", "variables", "async", "kyc", "risk", "settlement"}


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def validate_config(config: dict, require_cloud: bool = True, *, require_built_image: bool = True) -> list[str]:
    """Return actionable errors; never echo credentials or secret values."""
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["configuration must be an object"]
    if config.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be '1.0'")
    groups = {}
    for key in ("campaign", "artifact", "runtime", "workload"):
        value = config.get(key)
        if not isinstance(value, dict):
            errors.append(f"{key} must be an object")
            value = {}
        groups[key] = value
    campaign, artifact, runtime, workload = (groups[k] for k in ("campaign", "artifact", "runtime", "workload"))
    if not isinstance(campaign.get("name"), str) or not campaign["name"].strip():
        errors.append("campaign.name is required")
    if campaign.get("currency") != "USD":
        errors.append("campaign.currency must be USD for the v1 cost contract")
    if require_cloud:
        if not re.fullmatch(r"[a-z]{2}-[a-z0-9]+-\d+", str(campaign.get("region", ""))):
            errors.append("campaign.region must name a concrete OCI region")
        if not re.fullmatch(r"ocid1\.compartment\.[A-Za-z0-9._-]+", str(campaign.get("compartment_id", ""))):
            errors.append("campaign.compartment_id must be a concrete compartment OCID")
        if not is_number(campaign.get("budget_limit")) or campaign["budget_limit"] <= 0:
            errors.append("campaign.budget_limit must be a positive number before cloud provisioning")
        if campaign.get("access_method") not in {"resource_principal", "instance_principal", "config_file"}:
            errors.append("campaign.access_method must be resource_principal, instance_principal, or config_file")
        immutable_image = re.fullmatch(r"[^\s@]+@sha256:[a-fA-F0-9]{64}", str(artifact.get("image", "")))
        if not immutable_image:
            if require_built_image or artifact.get("image_source") != "maven-distribution":
                errors.append("artifact.image must be an immutable repository@sha256:digest reference")
            else:
                try:
                    distribution_url = urlsplit(artifact.get("distribution_url", ""))
                    if distribution_url.scheme != "https" or not distribution_url.netloc or distribution_url.username or distribution_url.password:
                        errors.append("artifact.distribution_url must be an HTTPS URL without credentials")
                except (TypeError, ValueError):
                    errors.append("artifact.distribution_url must be a valid HTTPS URL")
                if not re.fullmatch(r"[a-fA-F0-9]{64}", str(artifact.get("distribution_sha256", ""))):
                    errors.append("artifact.distribution_sha256 must be a 64-character SHA-256 digest")
                if not re.fullmatch(r"[^\s@]+@sha256:[a-fA-F0-9]{64}", str(artifact.get("java_image", ""))):
                    errors.append("artifact.java_image must be an immutable repository@sha256:digest reference")
        if not re.fullmatch(r"[a-fA-F0-9]{40}", str(artifact.get("source_commit", ""))):
            errors.append("artifact.source_commit must be a full 40-character commit SHA")
    if not isinstance(artifact.get("release"), str) or not artifact["release"].strip():
        errors.append("artifact.release is required (development builds must be labeled explicitly)")
    for key in ("cpu", "memory_gib", "heap_gib"):
        if not is_number(runtime.get(key)) or runtime[key] <= 0:
            errors.append(f"runtime.{key} must be positive")
    if is_number(runtime.get("heap_gib")) and is_number(runtime.get("memory_gib")):
        if runtime["heap_gib"] >= runtime["memory_gib"]:
            errors.append("runtime.heap_gib must be below memory_gib to leave room for non-heap memory")
    base_url = runtime.get("base_url")
    if base_url:
        try:
            url = urlsplit(base_url)
            if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
                errors.append("runtime.base_url must be an HTTP(S) URL without embedded credentials")
        except (TypeError, ValueError):
            errors.append("runtime.base_url must be a valid HTTP(S) URL")
    for key in ("auth_username_env", "auth_password_env"):
        if runtime.get(key) and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(runtime[key])):
            errors.append(f"runtime.{key} must be an environment-variable name")
    for key in ("password", "auth_password", "token", "private_key"):
        if key in runtime:
            errors.append(f"runtime.{key} must not contain a secret; use an environment-variable reference")
    if workload.get("scenario") not in SCENARIOS:
        errors.append("workload.scenario must be minimal, variables, async, kyc, risk, or settlement")
    for key in ("rate", "duration_s", "drain_s", "deadline_s"):
        if not is_number(workload.get(key)) or workload[key] <= 0:
            errors.append(f"workload.{key} must be positive")
    if not is_number(workload.get("warmup_s")) or workload["warmup_s"] < 0:
        errors.append("workload.warmup_s must be nonnegative")
    if "abort_on_warmup_unknown" in workload and not isinstance(workload["abort_on_warmup_unknown"], bool):
        errors.append("workload.abort_on_warmup_unknown must be a boolean")
    if "late_poll_interval_ms" in workload:
        late, normal = workload['late_poll_interval_ms'], workload.get('poll_interval_ms', 100)
        if not is_number(late) or late <= 0 or not is_number(normal) or normal <= 0:
            errors.append('workload.late_poll_interval_ms and poll_interval_ms must be positive numbers')
        elif late < normal:
            errors.append('workload.late_poll_interval_ms must be at least poll_interval_ms')
        elif is_number(workload.get('drain_s')) and late > workload['drain_s'] * 1000:
            errors.append('workload.late_poll_interval_ms must not exceed the observation budget')
    for key in ("seed", "payload_bytes"):
        if not isinstance(workload.get(key), int) or isinstance(workload.get(key), bool) or workload[key] < 0:
            errors.append(f"workload.{key} must be a nonnegative integer")
    run = config.get("run", {})
    if not isinstance(run, dict):
        errors.append("run must be an object")
    elif "mode" in run and run["mode"] not in {"live", "fixture", "local-functional"}:
        errors.append("run.mode must be live, fixture, or local-functional")
    estimated = config.get("estimated_cost")
    if estimated is not None:
        if not isinstance(estimated, dict):
            errors.append("estimated_cost must be an object")
        elif not is_number(estimated.get("amount")) or estimated["amount"] < 0:
            errors.append("estimated_cost.amount must be nonnegative")
        elif estimated.get("currency", "USD") != "USD":
            errors.append("estimated_cost.currency must be USD")
    return errors


def validate_event(event: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(event, dict):
        return ["event must be an object"]
    if event.get("event") not in EVENT_TYPES:
        errors.append("event has unsupported event type")
    for key in ("run_id", "logical_request_id", "scenario"):
        if not isinstance(event.get(key), str) or not event[key]:
            errors.append(f"event.{key} must be a nonempty string")
    if event.get("phase", "measurement") not in {"measurement", "warmup", "drain"}:
        errors.append("event.phase must be measurement, warmup, or drain")
    if "status" in event and event["status"] not in OUTCOME_STATUSES:
        errors.append("event.status is unsupported")
    if "at" in event and parse_timestamp(event["at"]) is None:
        errors.append("event.at must be an ISO-8601 timestamp with timezone")
    for key in ("elapsed_s", "latency_s", "deadline_s"):
        negative_allowed = key == "elapsed_s" and event.get("phase") == "warmup"
        if key in event and (not is_number(event[key]) or (event[key] < 0 and not negative_allowed)):
            errors.append(f"event.{key} must be a finite nonnegative number")
    if "at" not in event and "elapsed_s" not in event:
        errors.append("event requires at or elapsed_s")
    if not isinstance(event.get("details", {}), dict):
        errors.append("event.details must be an object")
    return errors
