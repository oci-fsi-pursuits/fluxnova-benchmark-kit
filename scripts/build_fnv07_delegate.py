#!/usr/bin/env python3
"""Compile the test delegate with pinned Fluxnova libraries and a supplied JDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_w02_functional import distribution_inventory


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(runtime, javac, output):
    runtime, javac, output = runtime.resolve(), javac.resolve(), output.resolve()
    if output.exists() or not javac.is_file():
        raise ValueError('Use a fresh output directory and an explicit verified compiler.')
    inventory = distribution_inventory(runtime)
    version = subprocess.check_output([str(javac), '-version'], text=True).strip()
    if not version.startswith('javac 21.'):
        raise ValueError('A Java 21 compiler is required.')
    output.mkdir(parents=True)
    dependencies = output / 'dependencies'
    dependencies.mkdir()
    core = runtime / 'internal/fluxnova-bpm-run-core.jar'
    with zipfile.ZipFile(core) as archive:
        for item in archive.infolist():
            if item.filename.startswith('BOOT-INF/lib/') and item.filename.endswith('.jar'):
                name = Path(item.filename).name
                if item.filename != 'BOOT-INF/lib/' + name or not 0 < item.file_size < 100 * 1024 * 1024:
                    raise ValueError('Unexpected distribution member')
                (dependencies / name).write_bytes(archive.read(item))
    classes = output / 'classes'
    classes.mkdir()
    source = ROOT / 'services/fnv07/RecordingDelegate.java'
    command = [str(javac), '--release', '21', '-encoding', 'UTF-8', '-g:none',
               '-classpath', os.pathsep.join(str(p) for p in sorted(dependencies.glob('*.jar'))),
               '-d', str(classes), str(source)]
    with (output / 'compiler.log').open('wb') as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=90)
    if process.returncode:
        raise RuntimeError('Delegate compilation failed; retained compiler.log')
    jar = output / 'fnv07-delegate.jar'
    members = sorted(classes.rglob('*.class'))
    if not members:
        raise RuntimeError('Compiler produced no class files')
    with zipfile.ZipFile(jar, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            info = zipfile.ZipInfo(member.relative_to(classes).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, member.read_bytes())
    result = {'artifact': jar.name, 'artifact_sha256': sha(jar), 'source_sha256': sha(source),
              'compiler_version': version, 'compiler_sha256': sha(javac), 'target_release': 21,
              'runtime': inventory, 'dependencies': {p.name: sha(p) for p in sorted(dependencies.glob('*.jar'))},
              'scope': 'Build receipt only; native engine behavior requires separate qualification.'}
    (output / 'build.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('artifact', 'artifact_sha256', 'source_sha256', 'compiler_version', 'target_release')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--javac', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.runtime, args.javac, args.output)
