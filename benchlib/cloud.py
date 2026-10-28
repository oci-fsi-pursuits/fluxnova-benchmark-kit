"""OCI Resource Manager adapter. No implicit apply; no credentials in state files."""
from __future__ import annotations
import base64
import io
import hashlib
import json
from pathlib import Path
import time
import zipfile
from .util import ROOT, read_json, write_json, sha256, utc_now

def package_stack(output):
    root = ROOT / "infra" / "oci-reference"
    if not root.is_dir():
        raise RuntimeError("OCI Terraform stack is missing")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [p for p in root.rglob("*") if p.is_file() and not any(part in (".terraform",) for part in p.parts)
             and p.suffix in {".tf", ".tftpl", ".yaml", ".yml", ".hcl"}]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(files):
            info = zipfile.ZipInfo(file.relative_to(root).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.read_bytes())
    return {"path": str(output), "sha256": sha256(output), "files": len(files), "cloud_resources_created": False}

def sdk_client(config, service="resource_manager"):
    try:
        import oci
    except ImportError:
        raise RuntimeError("Install the pinned cloud dependencies: python -m pip install -r requirements-cloud.txt") from None
    campaign = config["campaign"]
    method = campaign.get("access_method", "resource_principal")
    if method == "resource_principal":
        signer = oci.auth.signers.get_resource_principals_signer()
        sdk_config = {"region": campaign["region"]}
    elif method == "instance_principal":
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        sdk_config = {"region": campaign["region"]}
    elif method == "config_file":
        sdk_config = oci.config.from_file(profile_name=campaign.get("oci_profile", "DEFAULT"))
        sdk_config["region"] = campaign["region"]
        signer = None
    else:
        raise ValueError("access_method must be resource_principal, instance_principal, or config_file")
    classes = {"resource_manager": oci.resource_manager.ResourceManagerClient, "identity": oci.identity.IdentityClient,
               "compute": oci.core.ComputeClient, "psql": oci.psql.PostgresqlClient,
               "object_storage": oci.object_storage.ObjectStorageClient, "secrets": oci.secrets.SecretsClient,
               "limits": oci.limits.LimitsClient}
    client = classes[service](sdk_config, **({"signer": signer} if signer else {}))
    return oci, client

def online_preflight(config):
    oci, compute = sdk_client(config, "compute")
    compartment = config["campaign"]["compartment_id"]
    domain = config["oci"]["availability_domain"]
    shapes = oci.pagination.list_call_get_all_results(compute.list_shapes, compartment, availability_domain=domain).data
    shape = config.get("oci", {}).get("compute_shape", "VM.Standard.E5.Flex")
    if shape not in {item.shape for item in shapes}:
        raise RuntimeError(f"Compute shape {shape} unavailable in selected domain")
    _, psql = sdk_client(config, "psql")
    # Discovery is recorded; capacity/limits are checked again by the Terraform plan/apply service.
    available = oci.pagination.list_call_get_all_results(psql.list_shapes, compartment_id=compartment).data
    items = available.items if hasattr(available, "items") else available
    selected = config["oci"]["db_shape"]
    if selected not in {item.id for item in items}:
        raise RuntimeError("Configured PostgreSQL shape is unavailable in this region")
    quota = []
    requirements = config["oci"].get("quota_checks", [])
    if requirements:
        _, limits = sdk_client(config, "limits")
        for requirement in requirements:
            kwargs = {"availability_domain": domain} if requirement.get("scope") == "AD" else {}
            availability = limits.get_resource_availability(requirement["service"], requirement["limit"], compartment, **kwargs).data
            remaining = availability.fractional_availability
            if remaining is None:
                remaining = availability.available
            required = requirement["required"]
            if not isinstance(required, (float, int)) or isinstance(required, bool) or required <= 0:
                raise ValueError("Quota requirements must be positive resource amounts")
            if remaining is None or remaining < required:
                raise RuntimeError(f"Insufficient or unreported quota: {requirement['service']}/{requirement['limit']}")
            quota.append({**requirement, "available": remaining, "used": availability.used})
    categories = {r.get("category") for r in requirements}
    return {"checked_at": utc_now(), "compute_shape": shape, "postgres_shapes": oci.util.to_dict(available),
            "quota_checks": quota, "quota_status": "passed" if {"compute", "database", "storage", "network"} <= categories else "incomplete",
            "note": "Availability is a point-in-time check, not a capacity reservation; unsupported quota APIs need separate disclosed validation."}

def terraform_variables(config):
    c, o = config["campaign"], config["oci"]
    variables = {"region": c["region"], "compartment_id": c["compartment_id"], "availability_domain": o["availability_domain"],
                 "image_ocid": o["image_ocid"], "budget_limit_usd": str(c["budget_limit"]),
                 "ssh_authorized_key": o["ssh_authorized_key"], "db_admin_secret_id": o["db_admin_secret_id"],
                 "db_admin_username": o.get("db_admin_username", "benchadmin"), "db_shape": o["db_shape"],
                 "db_ocpu_count": str(o["db_ocpu_count"]), "db_memory_gb": str(o["db_memory_gb"]),
                 "db_storage_iops": str(o["db_storage_iops"])}
    if o.get("artifact_bucket_name"):
        variables["artifact_bucket_name"] = o["artifact_bucket_name"]
    variables.update({str(k): str(v) if not isinstance(v, (dict, list, bool)) else json.dumps(v)
                      for k, v in o.get("terraform_variables", {}).items()})
    # Inputs controlling placement/budget must not be overridden behind preflight's back.
    for key in ("region", "compartment_id", "availability_domain", "image_ocid", "budget_limit_usd"):
        if key in o.get("terraform_variables", {}):
            raise ValueError(f"Do not override protected Terraform variable {key}")
    return variables

def wait_job(client, job_id, maximum=3600):
    deadline = time.monotonic() + maximum
    while time.monotonic() < deadline:
        job = client.get_job(job_id).data
        if job.lifecycle_state == "SUCCEEDED":
            return job
        if job.lifecycle_state in ("FAILED", "CANCELED"):
            raise RuntimeError(f"Resource Manager job {job_id} {job.lifecycle_state}; inspect job logs")
        time.sleep(10)
    raise RuntimeError(f"Job {job_id} still running after timeout. Inspect it before retrying; timeout does not cancel or clean up resources.")

def provision(config, state_path, apply=False):
    oci, client = sdk_client(config)
    state_path = Path(state_path)
    m = oci.resource_manager.models
    if state_path.exists():
        state = read_json(state_path)
        if state["region"] != config["campaign"]["region"] or state["compartment_id"] != config["campaign"]["compartment_id"]:
            raise ValueError("Saved stack identity does not match configuration")
        if state.get("variables_sha256"):
            actual = hashlib.sha256(json.dumps(terraform_variables(config), sort_keys=True).encode()).hexdigest()
            if state["variables_sha256"] != actual:
                raise ValueError("Terraform inputs changed after planning; inspect and explicitly re-plan the existing stack")
        if state.get("apply_status") == "SUCCEEDED":
            return state
        if not state.get("plan_job_id"):
            raise RuntimeError("Stack exists without a recorded plan; inspect Resource Manager before retrying")
        wait_job(client, state["plan_job_id"])
        state["plan_status"] = "SUCCEEDED"
    else:
        archive = ROOT / "dist" / "oci-reference.zip"
        package = package_stack(archive)
        stack = client.create_stack(m.CreateStackDetails(
            compartment_id=config["campaign"]["compartment_id"], display_name=config["campaign"]["name"],
            config_source=m.CreateZipUploadConfigSourceDetails(zip_file_base64_encoded=base64.b64encode(archive.read_bytes()).decode()),
            variables=terraform_variables(config),
            freeform_tags={"benchmark": config["campaign"]["name"], "managed_by": "fluxnova-benchmarks"})).data
        state = {"stack_id": stack.id, "compartment_id": config["campaign"]["compartment_id"], "region": config["campaign"]["region"],
                 "package_sha256": package["sha256"], "created_at": utc_now(),
                 "variables_sha256": hashlib.sha256(json.dumps(terraform_variables(config), sort_keys=True).encode()).hexdigest()}
        write_json(state_path, state)
        plan = client.create_job(m.CreateJobDetails(stack_id=stack.id, operation="PLAN")).data
        state["plan_job_id"] = plan.id
        write_json(state_path, state)
        wait_job(client, plan.id)
        state["plan_status"] = "SUCCEEDED"
        write_json(state_path, state)
    if apply:
        started_apply = time.monotonic()
        if not state.get("apply_job_id"):
            job = client.create_job(m.CreateJobDetails(stack_id=state["stack_id"], operation="APPLY",
                apply_job_plan_resolution=m.ApplyJobPlanResolution(is_auto_approved=False, plan_job_id=state["plan_job_id"]))).data
            state["apply_job_id"] = job.id
            write_json(state_path, state)
        wait_job(client, state["apply_job_id"])
        # Raw Terraform state can contain secrets; retain only declared non-secret output contract.
        response = client.get_job_tf_state(state["apply_job_id"])
        state_data = response.data.content if hasattr(response.data, "content") else response.data
        tf_state = json.loads(state_data)
        contract = tf_state.get("outputs", {}).get("environment_contract", {}).get("value")
        if contract is None:
            raise RuntimeError("Apply succeeded but environment_contract output is missing; inspect saved stack")
        if isinstance(contract, str):
            contract = json.loads(contract)
        database_id = contract.get("resource_ids", {}).get("database")
        if database_id:
            _, psql = sdk_client(config, "psql")
            details = psql.get_connection_details(database_id).data
            endpoint = details.primary_db_endpoint
            if not endpoint.fqdn or not details.ca_certificate:
                raise RuntimeError("Database connection FQDN or trusted CA is missing; do not disable TLS verification")
            ca_file = state_path.parent / "postgres-ca.pem"
            ca_file.write_text(details.ca_certificate, encoding="utf-8")
            contract["database"].update(host=endpoint.fqdn, port=endpoint.port, ca_file=str(ca_file.resolve()),
                                        connection_details_source="OCI GetConnectionDetails")
        write_json(state_path.parent / "environment.local.json", contract)
        state["apply_status"] = "SUCCEEDED"
        state["apply_wait_elapsed_s"] = time.monotonic() - started_apply
        state["ready_at"] = utc_now()
        write_json(state_path, state)
    return state

def destroy(config, state_path, apply=False):
    state = read_json(state_path)
    if state["compartment_id"] != config["campaign"]["compartment_id"] or state["region"] != config["campaign"]["region"]:
        raise ValueError("Saved stack region/compartment does not match configuration")
    oci, client = sdk_client(config)
    stack = client.get_stack(state["stack_id"]).data
    if stack.compartment_id != state["compartment_id"]:
        raise ValueError("Live stack identity does not match saved deployment")
    if not apply:
        return {"status": "destroy_preview", "stack_id": stack.id, "requires": "Verified archived results and destroy --apply",
                "note": "Only this stack is targeted. Protected evidence resources need separate retention handling."}
    job = client.create_job(oci.resource_manager.models.CreateJobDetails(stack_id=stack.id, operation="DESTROY")).data
    state["destroy_job_id"] = job.id
    write_json(state_path, state)
    wait_job(client, job.id)
    state["destroy_status"] = "SUCCEEDED"
    write_json(state_path, state)
    return state
