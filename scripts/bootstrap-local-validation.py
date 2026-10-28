"""Download checksum-verified test tools into ignored .tools; never install globally."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"

def read(url):
    request = urllib.request.Request(url, headers={"User-Agent": "fluxnova-benchmark-validation"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()

def download(name, url, checksum):
    target = TOOLS / (name + ".zip")
    if not target.exists():
        target.write_bytes(read(url))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest.lower() != checksum.lower():
        raise ValueError(f"Checksum mismatch: {name}")
    destination = TOOLS / name
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(target) as archive:
        for member in archive.infolist():
            resolved = (destination / member.filename).resolve()
            if not resolved.is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive path")
        archive.extractall(destination)
    return {"name": name, "url": url, "sha256": digest, "directory": str(destination)}

def main():
    TOOLS.mkdir(exist_ok=True)
    flux_url = "https://repo.maven.apache.org/maven2/org/finos/fluxnova/bpm/run/fluxnova-bpm-run/3.0.1/fluxnova-bpm-run-3.0.1.zip"
    flux_sha = read(flux_url + ".sha256").decode().strip().split()[0]
    java = json.loads(read("https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jre&os=windows"))[0]["binary"]["package"]
    release = json.loads(read("https://api.github.com/repos/grafana/k6/releases/latest"))
    assets = release["assets"]
    k6 = next(a for a in assets if a["name"].endswith("windows-amd64.zip"))
    checksums = next(a for a in assets if "checksums" in a["name"].lower() and not a["name"].endswith((".sig", ".pem")))
    checksum_lines = read(checksums["browser_download_url"]).decode().splitlines()
    k6_sha = next(line.split()[0] for line in checksum_lines if k6["name"] in line)
    entries = [("fluxnova-3.0.1", flux_url, flux_sha), ("java21", java["link"], java["checksum"]), ("k6", k6["browser_download_url"], k6_sha)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        locks = list(pool.map(lambda args: download(*args), entries))
    (TOOLS / "validation-tools.lock.json").write_text(json.dumps(locks, indent=2), encoding="utf-8")
    print(json.dumps(locks, indent=2))

if __name__ == "__main__":
    main()
