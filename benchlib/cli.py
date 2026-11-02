from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
from .util import ROOT, read_json, write_json, utc_now

def output(value):
    print(json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False))

def parser():
    p = argparse.ArgumentParser(prog="bench", description="Reproducible Fluxnova benchmark lifecycle")
    commands = p.add_subparsers(dest="command", required=True)
    c = commands.add_parser("lab", help="Open a live BPMN lab on an isolated or existing Fluxnova engine")
    from .lab import add_arguments
    add_arguments(c)
    c = commands.add_parser("catalog", help="List benchmark types and studies; never launches work")
    c.add_argument("--check", action="store_true", help="Verify catalogue classification, files and evidence hashes")
    c = commands.add_parser("explain", help="Explain one benchmark study or diagram")
    c.add_argument("identifier")
    for name in ("preflight", "lock", "build", "deploy", "seed", "run", "campaign", "provision", "destroy", "golden", "database-check"):
        c = commands.add_parser(name)
        c.add_argument("--config", required=True, type=Path)
        if name in ("preflight", "deploy", "seed", "run", "campaign", "golden"):
            c.add_argument("--local", action="store_true", help="Functional local validation; not an OCI benchmark")
        if name == "preflight":
            c.add_argument("--online", action="store_true", help="Check OCI authentication and regional services")
        if name == "lock":
            c.add_argument("--output", required=True, type=Path)
            c.add_argument("--source", choices=("registry", "maven-distribution"), default="registry")
        if name == "build":
            c.add_argument("--native", action="store_true")
            c.add_argument("--publish", action="store_true", help="Push built images to configured OCIR repository using existing Docker login")
        if name == "deploy":
            c.add_argument("--runtime", action="store_true", help="Install the runtime container on this private role host")
            c.add_argument("--role", choices=("app", "support", "worker", "telemetry"), default="app")
            c.add_argument("--all-hosts", type=Path, metavar="ENVIRONMENT_JSON", help="Deploy private role hosts from their saved inventory over verified SSH")
        if name in ("run", "campaign"):
            c.add_argument("--output-root", type=Path, default=ROOT / "runs")
            c.add_argument("--run-id")
        if name == "campaign":
            c.add_argument("--profile", choices=("smoke", "discovery", "baseline", "soak", "acceptance"), default="smoke")
        if name == "seed":
            c.add_argument("--count", type=int, default=10)
        if name in ("golden", "database-check"):
            c.add_argument("--output", required=True, type=Path)
        if name in ("provision", "destroy"):
            c.add_argument("--apply", action="store_true", help="Execute reviewed Terraform plan; default creates plan only")
            c.add_argument("--state", type=Path, default=ROOT / "dist" / "oci-state.json")
    for name in ("verify", "report"):
        c = commands.add_parser(name)
        c.add_argument("run_dir", type=Path)
        if name == "report":
            c.add_argument("--output", type=Path)
            c.add_argument("--parquet", action="store_true", help="Also export outcomes.parquet; requires optional pyarrow")
    c = commands.add_parser("demo", help="Generate prominently labelled synthetic harness evidence")
    c.add_argument("--output-root", type=Path, default=ROOT / "runs")
    c = commands.add_parser("package", help="Create a Resource Manager ZIP without cloud actions")
    c.add_argument("--output", type=Path, default=ROOT / "dist" / "oci-reference.zip")
    c = commands.add_parser("compare", help="Analyze explicitly paired repeated runs")
    c.add_argument("--baseline", nargs="+", type=Path, required=True)
    c.add_argument("--candidate", nargs="+", type=Path, required=True)
    c.add_argument("--output", type=Path, required=True)
    c = commands.add_parser("archive", help="Create a deterministic archive; optionally upload to the retained bucket")
    c.add_argument("run_dir", type=Path)
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--config", type=Path, help="Upload to this configuration's retained OCI bucket")
    c = commands.add_parser("cost-import", help="Preserve and map billing CSV into a FOCUS 1.3-aligned export")
    c.add_argument("source", type=Path)
    c.add_argument("--mapping", required=True, type=Path)
    c.add_argument("--output", required=True, type=Path)
    c = commands.add_parser("configure", help="Resolve endpoints, TLS and registry from saved OCI inventory")
    c.add_argument("--config", required=True, type=Path)
    c.add_argument("--environment", required=True, type=Path)
    c.add_argument("--output", required=True, type=Path)
    return p

def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return execute(args)
    except (ValueError, RuntimeError, OSError, KeyError, ImportError) as exc:
        output({"status": "error", "message": str(exc)})
        return 2

def execute(args):
    if args.command == "lab":
        from .lab import serve
        return serve(args)
    if args.command in ("catalog", "explain"):
        from .catalog import load_catalog, validate_catalog, explain
        data = load_catalog()
        if args.command == "explain":
            output(explain(args.identifier, data))
        elif args.check:
            output(validate_catalog(data))
        else:
            output({"protocol_version": data["protocol_version"], "types": data["types"],
                    "studies": [{k:s[k] for k in ("id", "name", "type", "status", "claim_level", "result")} for s in data["studies"]],
                    "illustrated_patterns": [{k:p[k] for k in ("id", "name", "studies")} for p in data["patterns"]]})
        return 0
    from .preflight import check
    from .analysis import analyze_run
    from .report import write_reports
    from .runner import run_k6, verify_archive, demo_run
    if args.command == "archive":
        from .archive import archive_run
        output(archive_run(args.run_dir, args.output, read_json(args.config) if args.config else None))
        return 0
    if args.command == "cost-import":
        from .costs import import_costs
        output(import_costs(args.source, read_json(args.mapping), args.output))
        return 0
    if args.command == "compare":
        from .compare import compare_runs
        result = compare_runs(args.baseline, args.candidate, args.output)
        output(result)
        return 0 if result["status"] == "pass" else 1
    if args.command == "demo":
        run = demo_run(args.output_root)
        output({"run": run, "reports": write_reports(run), "warning": "SYNTHETIC HARNESS VALIDATION; NOT FLUXNOVA PERFORMANCE"})
        return 0
    if args.command == "report":
        paths = write_reports(args.run_dir, args.output)
        if args.parquet:
            from .report import write_parquet
            target = (args.output or args.run_dir / "reports") / "outcomes.parquet"
            paths["outcomes_parquet"] = write_parquet(args.run_dir, target)
        output(paths)
        return 0
    if args.command == "verify":
        changed = verify_archive(args.run_dir)
        result = analyze_run(args.run_dir)
        output({"archive_mismatches": changed, "analysis": result})
        return 0 if not changed and result.get("status") == "pass" else 1
    if args.command == "package":
        from .cloud import package_stack
        output(package_stack(args.output))
        return 0
    config = read_json(args.config)
    if args.command == "configure":
        from .environment import configure
        ready = configure(config, read_json(args.environment))
        write_json(args.output, ready)
        output({"config": args.output, "note": "No secrets were retrieved; private HTTP still requires its explicit lab setting"})
        return 0
    if args.command == "deploy" and args.all_hosts and not config["runtime"].get("base_url"):
        from .environment import configure
        config = configure(config, read_json(args.all_hosts))
    if args.command == "database-check":
        from .database import check_database
        output(check_database(config, args.output))
        return 0
    if args.command == "lock":
        from .artifacts import resolve_dockerhub, release_commit
        artifact = config.setdefault("artifact", {})
        release = artifact.get("release", "3.0.1")
        # No fallback to a different version on registry errors.
        artifact["image_source"] = args.source
        if args.source == "registry":
            artifact["image"] = resolve_dockerhub("finos/fluxnova-bpm-platform:" + release)
        else:
            import urllib.request
            url = f"https://repo.maven.apache.org/maven2/org/finos/fluxnova/bpm/run/fluxnova-bpm-run/{release}/fluxnova-bpm-run-{release}.zip"
            with urllib.request.urlopen(url + ".sha256", timeout=30) as response:
                digest = response.read().decode().strip().split()[0]
            artifact.update(image="", distribution_url=url, distribution_sha256=digest,
                            java_image=resolve_dockerhub("amazoncorretto:21"),
                            provenance="Published Maven distribution; upstream tag is a source reference, not verified build provenance")
        artifact["source_commit"] = release_commit(release)
        artifact["python_image"] = resolve_dockerhub("python:3.12.14-slim")
        artifact["k6_image"] = resolve_dockerhub("grafana/k6:2.3.0")
        artifact["otel_image"] = resolve_dockerhub("otel/opentelemetry-collector-contrib:0.137.0")
        artifact.update(javaagent_version="2.21.0",
            javaagent_url="https://repo.maven.apache.org/maven2/io/opentelemetry/javaagent/opentelemetry-javaagent/2.21.0/opentelemetry-javaagent-2.21.0.jar",
            javaagent_sha256="5b35ccc6c275fd477a06db01923813e13d99c1a52a20b0ea8ed6576eae177c7d")
        artifact["resolved_at"] = utc_now()
        write_json(args.output, config)
        output({"config": args.output, "artifact": artifact})
        return 0
    if args.command == "build":
        from .build import build_artifacts
        output(build_artifacts(config, native=args.native, publish=args.publish))
        return 0
    # Destroy needs saved stack identity, not a still-valid application configuration.
    if args.command == "destroy":
        from .cloud import destroy
        output(destroy(config, args.state, apply=args.apply))
        return 0
    errors = check(config, cloud=not getattr(args, "local", False), need_endpoint=args.command in ("run", "deploy", "seed", "campaign", "golden"))
    if errors:
        output({"status": "blocked", "errors": errors, "cloud_resources_created": False})
        return 2
    if args.command == "preflight":
        result = {"status": "passed", "scope": "local-functional" if args.local else "configuration-only", "cloud_resources_created": False}
        if args.online:
            from .cloud import online_preflight
            result["online"] = online_preflight(config)
            if result["online"]["quota_status"] != "passed":
                result["status"] = "inconclusive"
                output(result)
                return 1
        output(result)
        return 0
    if args.command == "provision":
        from .cloud import provision, online_preflight
        if args.apply:
            live = online_preflight(config)
            write_json(args.state.parent / "preflight-online.json", live)
            if live["quota_status"] != "passed":
                raise ValueError("Complete regional compute/database/storage/network quota checks before applying infrastructure")
        output(provision(config, args.state, apply=args.apply))
        return 0
    if args.command == "deploy" and args.all_hosts:
        from .remote import deploy_hosts
        output(deploy_hosts(config, read_json(args.all_hosts)))
        return 0
    if args.command == "deploy" and args.runtime:
        from .deployment import deploy_role
        output(deploy_role(config, args.role))
        return 0
    if args.command == "golden":
        from services.golden import run_golden
        from .util import repository_hashes, sha256
        runtime = config["runtime"]
        from .fluxnova import FluxnovaClient
        actual_version = FluxnovaClient.from_config(config).readiness().get("version")
        if actual_version != config["artifact"].get("expected_engine_version", config["artifact"]["release"]):
            raise ValueError("Golden suite engine version does not match the requested release")
        result = run_golden(runtime["base_url"], runtime["support_url"], args.output,
            username=os.environ.get(runtime.get("auth_username_env", "BENCH_USERNAME")),
            password=os.environ.get(runtime.get("auth_password_env", "BENCH_PASSWORD")), ca_file=runtime.get("ca_file"))
        result.update(artifact=config["artifact"], source_hashes=repository_hashes(),
                      mode="local-functional" if args.local else "live", endpoint=config["runtime"]["base_url"],
                      status="pass" if result.get("passed") else "failed")
        database_file = config.get("evidence", {}).get("database_file")
        if database_file:
            database = read_json(database_file)
            from .database import target_fingerprint
            if database.get("status") == "pass" and database.get("target_fingerprint") == target_fingerprint(config["database_probe"]):
                result["scope"] = {"image": config["artifact"].get("image"), "database_product": database["driver"],
                                   "database_version": " | ".join(database["actual_server_version"])}
                result["database_evidence_sha256"] = sha256(database_file)
                write_json(args.output / "database.json", database)
            else:
                raise ValueError("Database evidence is failed or belongs to another configured target")
        write_json(args.output / "correctness.json", result)
        output({"status": result["status"], "passed_cases": sum(c["status"] == "passed" for c in result["cases"]),
                "total_cases": len(result["cases"]), "report": args.output / "correctness.json", "scope": result.get("scope")})
        return 0 if result.get("status") == "pass" else 1
    from .fluxnova import FluxnovaClient
    client = FluxnovaClient.from_config(config)
    if args.command in ("deploy", "seed", "run", "campaign"):
        version = client.readiness().get("version")
        expected = config["artifact"].get("expected_engine_version", config["artifact"]["release"])
        if version != expected:
            raise ValueError(f"Actual engine version {version} does not match requested {expected}")
    if args.command == "deploy":
        from .fluxnova import workload_resources
        files = workload_resources(ROOT / "workloads")
        if not files:
            raise RuntimeError("No BPMN definitions found")
        started = time.monotonic()
        result = client.deploy(files, resource_root=ROOT / "workloads")
        from .util import sha256
        inventory = {"deployment": result, "elapsed_s": time.monotonic() - started, "created_at": utc_now(),
                     "files": {p.relative_to(ROOT / "workloads").as_posix(): sha256(p) for p in files}, "artifact": config["artifact"], "readiness": client.readiness()}
        path = ROOT / "dist" / ("deployment-" + str(time.time_ns()) + ".json")
        write_json(path, inventory)
        output({"inventory": path, "deployment_id": result["id"], "elapsed_s": inventory["elapsed_s"], "files": inventory["files"]})
        return 0
    if args.command == "seed":
        if not 0 <= args.count <= 100000:
            raise ValueError("seed count must be 0..100000")
        prefix = "seed-" + str(time.time_ns())
        for i in range(args.count):
            response = client.start("bench_minimal", f"{prefix}-{i}", {})
            if not client.observe(response["id"]):
                raise RuntimeError("Seed workflow did not complete")
        output({"seeded": args.count, "business_key_prefix": prefix})
        return 0
    if args.command == "campaign":
        from .campaign import execute_campaign
        result = execute_campaign(config, args.profile, args.output_root, local=args.local)
        output(result)
        return 0 if all(r['status'] == 'pass' for r in result['results']) else 1
    if args.command == "run":
        config.setdefault("run", {})["mode"] = "local-functional" if args.local else "live"
        run_id = args.run_id or "run-" + str(time.time_ns())
        directory = run_k6(config, run_id, args.output_root)
        output({"run": directory, "reports": write_reports(directory)})
        result = analyze_run(directory)
        return 0 if result.get("status") == "pass" else 1
    raise ValueError("Unknown command")

if __name__ == "__main__":
    sys.exit(main())
