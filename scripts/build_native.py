"""Build and run the upstream relative engine benchmark from an immutable SHA.

Requires Git, Maven, Java 21 and network/dependency access. Does not use the OCI
database automatically: choose the matching database profile and Maven settings.
"""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone


def run(argv, cwd, log):
    with log.open('ab') as stream:
        subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha', required=True, help='Full 40-character upstream commit, including for a release')
    parser.add_argument('--database', choices=['h2', 'postgresql', 'oracle'], default='h2')
    parser.add_argument('--history', choices=['full', 'none'], default='full')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--runs', type=int, default=10000)
    parser.add_argument('--workspace', default='.build/native')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--settings', help='External Maven settings file; never copy credentials into evidence')
    parser.add_argument('--git', default='git')
    parser.add_argument('--maven', default='mvn')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.sha) or min(args.threads, args.runs) < 1:
        parser.error('A full lowercase SHA and positive counts are required')
    output = Path(args.output_dir).resolve()
    if output.exists():
        parser.error('output-dir must be new; existing run evidence is never overwritten')
    output.mkdir(parents=True)
    work = Path(args.workspace).resolve() / args.sha
    if work.exists():
        parser.error('Source checkout already exists; choose a new workspace for a clean build')
    work.parent.mkdir(parents=True, exist_ok=True)
    log = output / 'native-build.log'
    metadata = {'schema_version': 1, 'kind': 'upstream_relative_engine_suite', 'sha': args.sha, 'database_profile': args.database, 'history': args.history, 'threads': args.threads, 'runs': args.runs, 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running', 'source': 'https://github.com/finos/fluxnova-bpm-platform.git', 'limitations': ['upstream relative regression suite; not remote API throughput or cross-cloud capacity', 'h2 is functional/local only' if args.database == 'h2' else 'database profile does not establish hosted-service compatibility']}
    manifest = output / 'native-build.json'
    manifest.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    try:
        for executable in ('java', args.git, args.maven):
            if shutil.which(executable) is None:
                raise RuntimeError('Native suite prerequisite unavailable: ' + executable + '; use a prepared Java21/Maven/Git builder')
        java = subprocess.run(['java', '-version'], capture_output=True, text=True, check=True)
        metadata['java_version'] = java.stderr.strip() or java.stdout.strip()
        if not re.search(r'version "21[.\"]', metadata['java_version']):
            raise RuntimeError('This build profile requires Java21; pin and verify another baseline separately')
        metadata['maven_version'] = subprocess.check_output([args.maven, '--version'], text=True).strip()
        metadata['git_version'] = subprocess.check_output([args.git, '--version'], text=True).strip()
        metadata['builder_os'] = platform.platform()
        metadata['builder_reproducibility'] = 'Host tool versions recorded; this invocation does not establish a digest-pinned builder image'
        metadata['dependency_cache'] = 'Existing Maven user repository; dependency cache is not reset'
        run([args.git, 'clone', '--no-checkout', metadata['source'], str(work)], None, log)
        run([args.git, 'checkout', '--detach', args.sha], work, log)
        actual = subprocess.check_output([args.git, 'rev-parse', 'HEAD'], cwd=work, text=True).strip()
        if actual != args.sha:
            raise RuntimeError('Source SHA mismatch')
        settings = ['--settings', str(Path(args.settings).resolve())] if args.settings else []
        if args.database != 'h2' and not args.settings:
            raise RuntimeError('Managed database runs require an explicit external Maven settings file')
        mvn = [args.maven, '--batch-mode', '--no-transfer-progress', *settings]
        run([*mvn, '-Pdistro', '-pl', 'qa/performance-tests-engine', '-am', 'clean', 'install', '-DskipTests'], work, log)
        run([*mvn, 'clean', 'install', '-Pbenchmark,' + args.database + ',history-level-' + args.history, '-DnumberOfThreads=' + str(args.threads), '-DnumberOfRuns=' + str(args.runs)], work / 'qa/performance-tests-engine', log)
        for name in ('reports', 'results'):
            src = work / 'qa/performance-tests-engine/target' / name
            if not src.is_dir():
                raise RuntimeError('Missing native suite output: ' + name)
            shutil.copytree(src, output / name)
        metadata['evidence_sha256'] = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file() and p != manifest}
        metadata['status'] = 'completed'
    except Exception as error:
        metadata['status'] = 'failed'
        metadata['error_type'] = type(error).__name__
        raise
    finally:
        metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
        manifest.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
