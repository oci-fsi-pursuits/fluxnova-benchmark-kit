from pathlib import Path
from contextlib import contextmanager
import json
import os
import re
import shutil
import subprocess
import sys
import time
from .util import ROOT, write_json, utc_now, sha256

@contextmanager
def build_lock():
    path = ROOT / "dist" / "build.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "started_at": utc_now()}))
    except FileExistsError:
        raise RuntimeError("Another build owns dist/build.lock; inspect its process before removing a stale lock") from None
    try:
        yield
    finally:
        path.unlink()

def build_artifacts(config, native=False, publish=False):
    with build_lock():
        return _build_artifacts(config, native=native, publish=publish)

def _build_artifacts(config, native=False, publish=False):
    artifact = config["artifact"]
    distribution = artifact.get("image_source") == "maven-distribution"
    for key in (("java_image", "python_image") if distribution else ("image", "python_image")):
        if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", artifact.get(key, "")):
            raise ValueError(f"artifact.{key} must be digest-pinned; run bench lock first")
    if not shutil.which("docker"):
        raise RuntimeError("Docker is required on the OCI controller for container builds; local source/report validation does not require Docker")
    directory = ROOT / "dist" / ("build-" + str(time.time_ns()))
    directory.mkdir(parents=True)
    started = time.monotonic()
    commands = []
    if distribution:
        import urllib.request
        import hashlib
        target = ROOT / "dist" / "fluxnova.zip"
        with urllib.request.urlopen(artifact["distribution_url"], timeout=180) as response:
            target.write_bytes(response.read())
        if sha256(target) != artifact["distribution_sha256"]:
            raise ValueError("Distribution checksum mismatch; refusing container build")
        if not re.fullmatch(r"[0-9a-f]{64}", artifact.get("javaagent_sha256", "")):
            raise ValueError("Pin the Java instrumentation agent with bench lock before building")
        agent = ROOT / "dist" / "opentelemetry-javaagent.jar"
        with urllib.request.urlopen(artifact["javaagent_url"], timeout=180) as response:
            agent.write_bytes(response.read())
        if sha256(agent) != artifact["javaagent_sha256"]:
            raise ValueError("Java instrumentation agent checksum mismatch")
        commands.append(["docker", "build", "--build-arg", "JAVA_IMAGE=" + artifact["java_image"],
                         "-f", "deploy/fluxnova.Dockerfile", "-t", "fluxnova-benchmark-runtime:" + artifact["release"], "."])
    else:
        commands.append(["docker", "pull", artifact["image"]])
    commands.append(["docker", "build", "--build-arg", "PYTHON_IMAGE=" + artifact["python_image"],
                 "-t", "fluxnova-benchmark-services:local", "-f", "services/Dockerfile", "."])
    if native:
        commit = artifact.get("source_commit", "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Full source_commit is required for a native build")
        commands.append([sys.executable, "scripts/build_native.py", "--sha", commit,
                         "--output-dir", str(directory / "native")])
    for i, command in enumerate(commands):
        with (directory / f"step-{i}.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=7200)
        if result.returncode:
            raise RuntimeError(f"Build step {i} failed; inspect {directory}")
    inspect = subprocess.run(["docker", "image", "inspect", "fluxnova-benchmark-services:local", "--format", "{{.Id}}"], capture_output=True, text=True, check=True)
    result = {"completed_at": utc_now(), "elapsed_s": time.monotonic() - started,
              "service_image_id": inspect.stdout.strip(), "fluxnova_image": artifact["image"],
              "native_build": native, "cache_state": "not_reset; warm/cold comparison requires separate explicit build environments"}
    if publish:
        import copy
        ready = copy.deepcopy(config)
        ready["artifact"]["javaagent_bundled"] = distribution
        repository = artifact.get("publish_repository", "")
        if not re.fullmatch(r"(?:[a-z0-9-]+\.ocir\.io|ocir\.[a-z0-9-]+\.oci\.oraclecloud\.com)/[a-zA-Z0-9/_-]+", repository):
            raise ValueError("artifact.publish_repository must identify the provisioned OCIR repository")
        images = [("services_image", "fluxnova-benchmark-services:local", repository + ":services-" + str(time.time_ns()))]
        if distribution:
            images.append(("image", "fluxnova-benchmark-runtime:" + artifact["release"], repository + ":runtime-" + str(time.time_ns())))
        for field, source, target in images:
            subprocess.run(["docker", "tag", source, target], check=True)
            subprocess.run(["docker", "push", target], check=True)
            raw = subprocess.run(["docker", "image", "inspect", target, "--format", "{{json .RepoDigests}}"], check=True, capture_output=True, text=True).stdout.strip()
            matches = [d for d in json.loads(raw) if d.startswith(repository + "@sha256:")]
            if len(matches) != 1:
                raise RuntimeError("Expected one image digest matching the published repository")
            ready["artifact"][field] = matches[0]
        write_json(directory / "config.ready.json", ready)
        result["ready_config"] = str(directory / "config.ready.json")
        result["fluxnova_image"] = ready["artifact"]["image"]
        result["services_image"] = ready["artifact"]["services_image"]
    write_json(directory / "build.json", result)
    return {"directory": str(directory), **result}
