"""Install pinned runtime containers on the selected private role host."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from .cloud import sdk_client
from .util import ROOT, utc_now

SERVICES_NETWORK = "fluxnova-benchmark-services"
# Operational logs are a rolling buffer, not the sealed benchmark evidence.
CONTAINER_LOG_OPTIONS = ("--log-driver", "json-file", "--log-opt", "max-size=100m", "--log-opt", "max-file=5")


def services_network():
    """Keep same-host support traffic off the VM's published-port hairpin."""
    result = subprocess.run(["docker", "network", "inspect", SERVICES_NETWORK], capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        network = json.loads(result.stdout)[0]
        if network.get("Driver") != "bridge" or (network.get("Labels") or {}).get("managed_by") != "fluxnova-benchmarks":
            raise RuntimeError("Existing services network is not managed by this deployment")
    else:
        subprocess.run(["docker", "network", "create", "--driver", "bridge", "--label", "managed_by=fluxnova-benchmarks", SERVICES_NETWORK], check=True, capture_output=True, timeout=30)
    return SERVICES_NETWORK

def secret(config, ocid):
    if not ocid:
        raise ValueError("A Vault secret OCID is required; secret values must not be stored in configuration")
    _, client = sdk_client(config, "secrets")
    bundle = client.get_secret_bundle(ocid).data.secret_bundle_content
    return base64.b64decode(bundle.content).decode()

def run_container(name, image, env, options=(), args=()):
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
        raise ValueError(f"{name} requires a registry image pinned by digest")
    if any(str(option).split("=", 1)[0] in ("--log-driver", "--log-opt") for option in options):
        raise ValueError("Container logging is bounded by the deployment policy; log overrides are not allowed")
    # Refuse implicit replacement of an existing container, preserving an active run.
    exists = subprocess.run(["docker", "container", "inspect", name], capture_output=True)
    if exists.returncode == 0:
        raise RuntimeError(f"Container {name} already exists; stop/archive it explicitly before redeploying")
    with tempfile.TemporaryDirectory(prefix="benchmark-env-") as temp:
        path = Path(temp) / "runtime.env"
        for key, value in env.items():
            if not re.fullmatch(r"[A-Z0-9_]+", key) or any(c in str(value) for c in ("\n", "\r", "\0")):
                raise ValueError("Invalid container environment value")
        path.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
        os.chmod(path, 0o600)
        command = ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped", *CONTAINER_LOG_OPTIONS, "--env-file", str(path), *options, image, *args]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=300)
        return result.stdout.strip()

def deploy_role(config, role):
    runtime = config["runtime"]
    artifacts = config["artifact"]
    if role == "app":
        jdbc = runtime.get("jdbc_url", "")
        if not jdbc.startswith("jdbc:postgresql://") or "sslmode=verify-full" not in jdbc or "sslrootcert=/run/db-ca.pem" not in jdbc:
            raise ValueError("runtime.jdbc_url must use PostgreSQL sslmode=verify-full and a trusted root certificate")
        ca = Path(runtime.get("db_ca_file", ""))
        if not ca.is_file():
            raise ValueError("runtime.db_ca_file must exist on the application host")
        password = secret(config, runtime.get("db_password_secret_id") or config["oci"]["db_admin_secret_id"])
        admin_password = secret(config, runtime.get("admin_password_secret_id"))
        env = {"SPRING_DATASOURCE_URL": jdbc, "SPRING_DATASOURCE_USERNAME": runtime.get("db_user") or config["oci"].get("db_admin_username", "benchadmin"),
               "SPRING_DATASOURCE_PASSWORD": password, "SPRING_DATASOURCE_DRIVER_CLASS_NAME": "org.postgresql.Driver",
               "FLUXNOVA_BPM_HISTORY_LEVEL": runtime.get("history_level", "full"),
               "FLUXNOVA_BPM_RUN_AUTH_ENABLED": "true", "FLUXNOVA_BPM_AUTHORIZATION_ENABLED": "true",
               "FLUXNOVA_BPM_ADMIN_USER_ID": runtime.get("admin_username", "benchmark"), "FLUXNOVA_BPM_ADMIN_USER_PASSWORD": admin_password,
               "JAVA_OPTS": f"-Xms{runtime['heap_gib']}g -Xmx{runtime['heap_gib']}g",
               "SPRING_DATASOURCE_HIKARI_MAXIMUMPOOLSIZE": str(runtime.get("db_pool_size", 20))}
        options = ["--cpus", str(runtime["cpu"]), "--memory", str(runtime["memory_gib"]) + "g",
                   "-p", "8080:8080", "-v", f"{ca.resolve()}:/run/db-ca.pem:ro"]
        if runtime.get("telemetry_enabled", True):
            if artifacts.get("javaagent_bundled") is not True:
                raise ValueError("Telemetry requires the bundled, checksum-pinned Java agent; build the distribution image first")
            endpoint = runtime.get("otlp_endpoint", "")
            if not endpoint.startswith(("http://", "https://")):
                raise ValueError("runtime.otlp_endpoint is required when instrumentation is enabled")
            env.update(JAVA_TOOL_OPTIONS="-javaagent:/opt/opentelemetry-javaagent.jar",
                       OTEL_EXPORTER_OTLP_ENDPOINT=endpoint, OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
                       OTEL_SERVICE_NAME="fluxnova", OTEL_METRICS_EXPORTER="otlp", OTEL_TRACES_EXPORTER="otlp", OTEL_LOGS_EXPORTER="none",
                       OTEL_RESOURCE_ATTRIBUTES=f"cloud.provider=oracle_cloud,cloud.platform=oracle_cloud_compute,cloud.region={config['campaign']['region']},service.version={artifacts['release']}",
                       OTEL_METRIC_EXPORT_INTERVAL="10000", OTEL_TRACES_SAMPLER="parentbased_traceidratio", OTEL_TRACES_SAMPLER_ARG="0.1")
        container = run_container("fluxnova-benchmark", artifacts["image"], env, options)
    elif role == "support":
        container = run_container("benchmark-support", artifacts.get("services_image", ""),
            {"BENCH_BIND": "0.0.0.0", "BENCH_PORT": "8090", "BENCH_DB": "/data/ledger.sqlite",
             "BENCH_SUPPORT_TOKEN": secret(config, runtime.get("support_token_secret_id"))},
            ["--network", services_network(), "--cpus", "2", "--memory", "2g", "-p", "8090:8090", "-v", "benchmark-ledger:/data"])
    elif role == "worker":
        timing = runtime.get("worker_timing_enabled", False)
        if not isinstance(timing, bool):
            raise ValueError("runtime.worker_timing_enabled must be a boolean")
        env = {"BENCH_BASE_URL": runtime["base_url"], "BENCH_SUPPORT_URL": runtime.get("worker_support_url", runtime["support_url"]),
               "BENCH_USERNAME": runtime.get("admin_username", "benchmark"),
               "BENCH_PASSWORD": secret(config, runtime.get("admin_password_secret_id")),
               "BENCH_SUPPORT_TOKEN": secret(config, runtime.get("support_token_secret_id"))}
        if timing:
            env["BENCH_WORKER_TIMING"] = "1"
        options = ["--network", services_network(), "--cpus", "2", "--memory", "2g"]
        for field, target in (("ca_file", "BENCH_ENGINE_CA_FILE"), ("support_ca_file", "BENCH_SUPPORT_CA_FILE")):
            if runtime.get(field):
                source = Path(runtime[field])
                if not source.is_file():
                    raise ValueError("Configured worker CA certificate does not exist")
                mounted = "/run/" + field + ".pem"
                options += ["-v", f"{source.resolve()}:{mounted}:ro"]
                env[target] = mounted
        container = run_container("benchmark-worker", artifacts.get("worker_image", artifacts.get("services_image", "")),
                                  env, options, ["python", "-m", "services.worker"])
    elif role == "telemetry":
        collector = ROOT / "telemetry" / "collector.yaml"
        if not collector.is_file():
            raise ValueError("OpenTelemetry collector configuration is missing")
        data = ROOT / "runs" / "telemetry"
        data.mkdir(parents=True, exist_ok=True)
        os.chmod(data, 0o700)
        container = run_container("benchmark-telemetry", artifacts.get("otel_image", ""),
            {"BENCH_OCI_REGION": config["campaign"]["region"]},
            ["--user", f"{os.getuid()}:{os.getgid()}", "--cpus", "1", "--memory", "1g", "-p", "4318:4318", "-p", "127.0.0.1:13133:13133",
             "-v", f"{collector.resolve()}:/etc/otelcol-contrib/config.yaml:ro", "-v", f"{data.resolve()}:/data"],
            ["--config=/etc/otelcol-contrib/config.yaml"])
    else:
        raise ValueError("role must be app, support, worker, or telemetry")
    return {"role": role, "container_id": container, "created_at": utc_now(), "readiness": "Run bench preflight --online and REST readiness before deployment of BPMN"}
