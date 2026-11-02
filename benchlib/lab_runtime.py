"""Owned, loopback-only Fluxnova runtime for hands-on functional exploration."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request
import uuid
import zipfile

from .fluxnova import FluxnovaClient
from .util import ROOT

RELEASE = '3.0.1'
SHA256 = 'ced4311c541faf1a7248d88e37ba53bd8c659fefe8387d2f78c21c30a117d7cf'
URL = f'https://repo.maven.apache.org/maven2/org/finos/fluxnova/bpm/run/fluxnova-bpm-run/{RELEASE}/fluxnova-bpm-run-{RELEASE}.zip'


def prepare_runtime(runtime, archive, download=False):
    runtime, archive = Path(runtime), Path(archive)
    if not archive.is_file() and download:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(URL, timeout=180) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError('Fluxnova distribution checksum mismatch.')
        archive.write_bytes(data)
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Pinned Fluxnova ZIP missing or changed. Use --download-runtime to obtain the official 3.0.1 ZIP.')
    with zipfile.ZipFile(archive) as bundle:
        if not runtime.exists():
            runtime.mkdir(parents=True)
            for entry in bundle.infolist():
                if not (runtime / entry.filename).resolve().is_relative_to(runtime.resolve()):
                    raise ValueError('Unsafe distribution path.')
            bundle.extractall(runtime)
        expected = {n for n in bundle.namelist() if n.endswith('.jar')}
        actual = {p.relative_to(runtime).as_posix() for p in runtime.rglob('*.jar')}
        if actual != expected or any(hashlib.sha256((runtime / n).read_bytes()).digest() != hashlib.sha256(bundle.read(n)).digest() for n in expected):
            raise ValueError('Runtime JARs differ from the pinned distribution.')
    return runtime.resolve()


def find_java():
    installed = shutil.which('java')
    if installed:
        return Path(installed)
    for root in (ROOT / '.tools/java21', ROOT / '.tools/java21-jdk'):
        paths = sorted(root.glob('**/bin/java.exe')) + sorted(root.glob('**/bin/java'))
        if paths:
            return paths[0]
    raise ValueError('Java 21 is required. Install it or supply --java PATH_TO_JAVA.')


class LocalRuntime:
    def __init__(self, folder, runtime, archive, java=None, port=18088, download=False):
        self.folder, self.process, self.log, self.config = Path(folder), None, None, None
        self.runtime, self.archive = runtime, archive
        self.java, self.port, self.download = java, port, download
        self.password = uuid.uuid4().hex

    def start(self):
        runtime = prepare_runtime(self.runtime, self.archive, self.download)
        java = Path(self.java) if self.java else find_java()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', self.port))  # Refuse to reuse another engine.
        self.folder.mkdir(parents=True, exist_ok=False)
        empty = self.folder / 'empty-deployments'
        empty.mkdir()
        self.config = self.folder / 'engine.local.yml'
        self.config.write_text(
            f'server:\n  address: 127.0.0.1\n  port: {self.port}\n'
            'fluxnova.bpm:\n  admin-user:\n    id: lab\n    password: ' + json.dumps(self.password) + '\n'
            '  history-level: full\n  authorization:\n    enabled: true\n  filter:\n    create: All tasks\n'
            '  run:\n    auth:\n      enabled: true\n    cors:\n      enabled: false\n    example:\n      enabled: false\n'
            'spring.datasource:\n  url: ' + json.dumps('jdbc:h2:' + (self.folder / 'engine-db').resolve().as_posix() + ';DB_CLOSE_ON_EXIT=FALSE') + '\n'
            '  driver-class-name: org.h2.Driver\n  username: sa\n  password: sa\n'
            'spring.web.resources:\n  static-locations: NULL\n', encoding='utf-8')
        os.chmod(self.config, 0o600)
        self.log = (self.folder / 'engine.log').open('w', encoding='utf-8')
        self.process = subprocess.Popen([str(java), '-Xms256m', '-Xmx1024m',
            '-Dloader.path=' + ','.join(str(runtime / p) for p in ('internal/rest', 'internal/webapps', 'configuration/userlib')),
            '-Dcamunda.deploymentDir=' + str(empty.resolve()), '-jar', str(runtime / 'internal/fluxnova-bpm-run-core.jar'),
            '--spring.config.location=' + self.config.resolve().as_uri()], cwd=self.folder,
            stdout=self.log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        client = FluxnovaClient(f'http://127.0.0.1:{self.port}/engine-rest', 'lab', self.password)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Owned engine exited; inspect the local engine.log.')
            try:
                client.readiness()
                return client
            except (OSError, RuntimeError):
                time.sleep(.5)
        raise RuntimeError('Owned engine did not become ready within 180 seconds.')

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self.log:
            self.log.close()
        if self.config and self.config.is_file():
            self.config.write_text(self.config.read_text(encoding='utf-8').replace(self.password, 'REDACTED_AFTER_SHUTDOWN'), encoding='utf-8')
