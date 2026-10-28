from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def checked_run(args, *, cwd=ROOT, env=None, timeout=300):
    return subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True, timeout=timeout)

def repository_hashes():
    files = {}
    for folder in ("benchlib", "schemas", "load", "workloads", "services", "infra", "deploy", "scripts", "telemetry"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and not any((x.startswith(".") and x != ".terraform.lock.hcl") or x == "__pycache__" for x in path.relative_to(ROOT).parts):
                files[path.relative_to(ROOT).as_posix()] = sha256(path)
    return files

def auth_environment(config):
    runtime = config.get("runtime", {})
    result = os.environ.copy()
    for target, source_key in (("BENCH_USERNAME", "auth_username_env"), ("BENCH_PASSWORD", "auth_password_env")):
        source = runtime.get(source_key, target)
        if source in os.environ:
            result[target] = os.environ[source]
    return result

def safe_run_id(value):
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise ValueError("run ID must be 1-100 letters, numbers, dots, underscores or hyphens")
    return value
