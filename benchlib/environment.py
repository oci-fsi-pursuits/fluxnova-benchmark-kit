"""Materialize non-secret runtime configuration from reviewed cloud inventory."""
import copy
from pathlib import Path
import re


def configure(config, environment):
    result = copy.deepcopy(config)
    for key in ("region", "compartment_id"):
        if environment.get(key) != config["campaign"].get(key):
            raise ValueError("Environment placement does not match campaign")
    database = environment["database"]
    host, port = database["host"], database["port"]
    name = database.get("database", "postgres")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host) or not isinstance(port, int) or not 1 <= port <= 65535 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Database inventory endpoint is malformed")
    if not database.get("ca_file") or not Path(database["ca_file"]).is_file():
        raise ValueError("Provisioning must first resolve the database service FQDN and public CA")
    runtime = result["runtime"]
    runtime.update(base_url=environment["base_url"], support_url=environment["support_url"],
        db_ca_file=database["ca_file"],
        jdbc_url=f"jdbc:postgresql://{host}:{port}/{name}?sslmode=verify-full&sslrootcert=/run/db-ca.pem",
        otlp_endpoint="http://" + environment["roles"]["support"]["private_ip"] + ":4318")
    registry = environment["registry"]
    result["artifact"]["publish_repository"] = "/".join(registry[k] for k in ("host", "namespace", "repository"))
    result["oci"].update(artifact_namespace=environment["artifacts"]["namespace"], artifact_bucket_name=environment["artifacts"]["bucket"])
    ca = database["ca_file"].replace("\\", "\\\\").replace("'", "\\'")
    result["database_probe"] = {"driver": "postgresql", "dsn": f"host={host} port={port} dbname={name} sslmode=verify-full sslrootcert='{ca}'",
        "user": database["admin_username"], "password_secret_id": database["admin_secret_id"], "password_env": "BENCH_DB_PASSWORD"}
    result["environment"] = environment
    return result
