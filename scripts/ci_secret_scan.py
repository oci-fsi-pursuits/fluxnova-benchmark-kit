#!/usr/bin/env python3
"""Run a pinned Gitleaks history scan with redacted output on Linux x86_64 CI."""
import hashlib
import platform
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def main():
    assert platform.system()=='Linux' and platform.machine().lower()=='x86_64'
    dest=ROOT/'.tools/gitleaks-ci';dest.mkdir(parents=True,exist_ok=True)
    archive=dest/'gitleaks.tar.gz'
    req=urllib.request.Request('https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz',headers={'User-Agent':'fluxnova-ci'})
    with urllib.request.urlopen(req,timeout=60) as response:data=response.read()
    assert hashlib.sha256(data).hexdigest()=='551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb'
    archive.write_bytes(data)
    with tarfile.open(archive) as t:
        assert all((dest/m.name).resolve().is_relative_to(dest.resolve()) and not m.issym() and not m.islnk() for m in t.getmembers())
        t.extractall(dest,filter='data')
    binary=dest/'gitleaks';binary.chmod(0o755)
    subprocess.run([str(binary),'git',str(ROOT),'--log-opts=--all','--redact=100','--no-banner','--timeout=180'],cwd=ROOT,check=True)
    candidate=ROOT/'dist/ci-public-candidate'
    subprocess.run([sys.executable,str(ROOT/'scripts/build_public_candidate.py'),'--output',str(candidate)],cwd=ROOT,check=True)
    subprocess.run([str(binary),'dir',str(candidate),'--redact=100','--no-banner','--timeout=180'],cwd=candidate,check=True)


if __name__=='__main__':main()
