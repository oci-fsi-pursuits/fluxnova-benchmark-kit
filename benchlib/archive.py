"""Archive sealed run evidence locally or in the configured retained OCI bucket."""
import base64
import hashlib
from pathlib import Path
import shutil
import tempfile
import zipfile
from .runner import verify_archive
from .util import read_json, sha256, utc_now, write_json


def archive_run(run_dir, destination, config=None):
    run_dir, destination = Path(run_dir).resolve(), Path(destination).resolve()
    if destination.is_relative_to(run_dir):
        raise ValueError("Archive destination must be outside the evidence directory")
    mismatches = verify_archive(run_dir)
    if mismatches:
        raise ValueError("Cannot archive modified evidence: " + ", ".join(mismatches))
    manifest = read_json(run_dir / "manifest.json")
    files = sorted(p for p in run_dir.rglob("*") if p.is_file())
    if any(p.is_symlink() or not p.resolve().is_relative_to(run_dir) for p in files):
        raise ValueError("Evidence must not link outside its run directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Recompute the deterministic ZIP, then reuse only byte-identical existing
    # content. An upload retry never overwrites a different local archive.
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".bench-archive-", suffix=".zip", delete=False) as temporary:
        generated = Path(temporary.name)
    try:
        with zipfile.ZipFile(generated, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info = zipfile.ZipInfo(path.relative_to(run_dir).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        generated_sha256 = sha256(generated)
        created = False
        try:
            with destination.open("xb") as stream:
                created = True
                with generated.open("rb") as source:
                    shutil.copyfileobj(source, stream)
        except FileExistsError:
            if not destination.is_file() or sha256(destination) != generated_sha256:
                raise ValueError("Existing archive differs from the current sealed evidence; choose a new destination") from None
        except BaseException:
            if created:
                destination.unlink()
            raise
    finally:
        generated.unlink(missing_ok=True)
    receipt = {"run_id": manifest["run_id"], "created_at": utc_now(), "path": str(destination),
               "sha256": sha256(destination), "bytes": destination.stat().st_size, "file_count": len(files),
               "cloud_upload": False}
    if config:
        from .cloud import sdk_client
        _, client = sdk_client(config, "object_storage")
        storage = config["oci"]
        namespace, bucket = storage["artifact_namespace"], storage["artifact_bucket_name"]
        name = f"{config['campaign']['name']}/{manifest['run_id']}/{receipt['sha256']}.zip"
        with destination.open("rb") as stream:
            md5 = hashlib.file_digest(stream, "md5").digest()
            stream.seek(0)
            try:
                response = client.put_object(namespace, bucket, name, stream,
                    content_length=receipt["bytes"], content_type="application/zip",
                    content_md5=base64.b64encode(md5).decode(), if_none_match="*",
                    opc_meta={"sha256": receipt["sha256"], "run-id": manifest["run_id"]})
            except Exception as exc:
                if getattr(exc, "status", None) != 412:
                    raise
                # A prior upload may have succeeded before its response was lost.
                response = client.head_object(namespace, bucket, name)
                if response.headers.get("opc-meta-sha256") != receipt["sha256"] or str(response.headers.get("content-length")) != str(receipt["bytes"]):
                    raise ValueError("Existing OCI object does not match the local sealed archive") from None
        receipt.update(cloud_upload=True, namespace=namespace, bucket=bucket, object_name=name,
                       etag=response.headers.get("etag"), version_id=response.headers.get("version-id"))
    write_json(destination.with_suffix(".receipt.json"), receipt)
    return receipt
