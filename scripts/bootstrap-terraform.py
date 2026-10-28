"""Install a checksum-verified Terraform binary inside this workspace only."""
from pathlib import Path
import hashlib
import urllib.request
import zipfile
import json

root = Path(__file__).resolve().parents[1]
version = "1.13.5"
name = f"terraform_{version}_windows_amd64.zip"
base = f"https://releases.hashicorp.com/terraform/{version}/"
target = root / ".tools" / "terraform"
target.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(base + f"terraform_{version}_SHA256SUMS", timeout=60) as response:
    expected = next(line.split()[0] for line in response.read().decode().splitlines() if line.endswith(name))
with urllib.request.urlopen(base + name, timeout=180) as response:
    content = response.read()
actual = hashlib.sha256(content).hexdigest()
if actual != expected:
    raise RuntimeError("Terraform checksum mismatch")
archive = target / name
archive.write_bytes(content)
with zipfile.ZipFile(archive) as z:
    (target / "terraform.exe").write_bytes(z.read("terraform.exe"))
(target / "provenance.json").write_text(json.dumps({"version": version, "url": base + name, "sha256": actual}, indent=2), encoding="utf-8")
print(json.dumps({"terraform": str(target / "terraform.exe"), "sha256": actual}))
