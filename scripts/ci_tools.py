#!/usr/bin/env python3
"""Install pinned, checksum-verified k6 for the optional integration tests."""
import hashlib
import os
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ASSETS={
    'Windows':('k6-v2.3.0-windows-amd64.zip','112276d495e5741c968e2bc09ea6196099c1275bd6db9ee0875d173c7148ce43','k6-v2.3.0-windows-amd64/k6.exe'),
    'Linux':('k6-v2.3.0-linux-amd64.tar.gz','39c3117b6af817592dcd0ce4242105c0a7af10948c2a425306f0be8f7a8a8ab1','k6-v2.3.0-linux-amd64/k6')}


def main():
    if platform.machine().lower() not in ('amd64','x86_64'):raise SystemExit('This CI helper pins x86_64 binaries only.')
    name,digest,executable=ASSETS[platform.system()];dest=ROOT/'.tools/ci';dest.mkdir(parents=True,exist_ok=True)
    archive=dest/name
    if not archive.exists():
        req=urllib.request.Request('https://github.com/grafana/k6/releases/download/v2.3.0/'+name,headers={'User-Agent':'fluxnova-ci'})
        with urllib.request.urlopen(req,timeout=90) as response:archive.write_bytes(response.read())
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==digest,'k6 archive checksum mismatch'
    if name.endswith('.zip'):
        with zipfile.ZipFile(archive) as z:
            assert all((dest/n).resolve().is_relative_to(dest.resolve()) for n in z.namelist())
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            assert all((dest/m.name).resolve().is_relative_to(dest.resolve()) and not m.issym() and not m.islnk() for m in t.getmembers())
            t.extractall(dest,filter='data')
    binary=(dest/executable).resolve();assert binary.is_file()
    if os.name!='nt':binary.chmod(0o755)
    if os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'],'a',encoding='utf-8') as f:f.write('BENCH_TEST_K6_BINARY='+str(binary)+'\n')
    print(binary)


if __name__=='__main__':main()
