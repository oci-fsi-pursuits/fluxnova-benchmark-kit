"""Build and run the upstream relative engine benchmark from an immutable SHA.

Requires Git, Maven, the declared JDK and network/dependency access. Does not use the OCI
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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def run(argv, cwd, log, timeout):
    with log.open('ab') as stream:
        subprocess.run(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=timeout)


def retain_results(work, output):
    """Keep partial evidence on failure as well as complete successful output."""
    target = work / 'qa/performance-tests-engine/target'
    for name in ('reports', 'results', 'surefire-reports'):
        source = target / name
        if source.is_dir():
            shutil.copytree(source, output / name, dirs_exist_ok=True)


def validate_results(output):
    """A successful Maven exit alone does not prove that tests ran."""
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    xml_files = sorted((output / 'surefire-reports').glob('TEST-*.xml'))
    for path in xml_files:
        for case in ET.parse(path).getroot().iter('testcase'):
            counts['tests'] += 1
            for field, element in (('failures', 'failure'), ('errors', 'error'),
                                   ('skipped', 'skipped')):
                counts[field] += case.find(element) is not None
    if (not xml_files or counts['tests'] <= counts['skipped']
            or counts['failures'] or counts['errors']):
        raise RuntimeError('Native tests missing, all skipped, or unsuccessful')
    raw = sorted((output / 'results').rglob('*.json'))
    reports = list((output / 'reports').rglob('*.html'))
    if not raw or not reports:
        raise RuntimeError('Missing native benchmark results or HTML report')
    for path in raw:
        result = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(result, dict) or not result.get('testName')
                or not isinstance(result.get('passResults'), list)
                or not result['passResults']):
            raise RuntimeError('Incomplete native benchmark result')
    return {'junit': counts, 'junit_files': len(xml_files),
            'native_result_files': len(raw), 'html_reports': len(reports)}


def add_history_fixture(work, output, days):
    """Supply a declared retention default through the upstream plugin hook."""
    if type(days) is not int or not 1 <= days <= 36500:
        raise ValueError('History retention must be a positive number of days, at most 36500')
    package = 'org.finos.fluxnova.bpm.qa.performance.engine.benchmarkkit'
    name = 'BenchmarkHistoryRetentionPlugin'
    relative = Path('qa/performance-tests-engine/src/test/java') / package.replace('.', '/') / (name + '.java')
    target = work / relative
    if target.exists():
        raise ValueError('History fixture must not replace an upstream source file')
    source = f'''// Benchmark-kit fixture extension, licensed under Apache-2.0.
package {package};

import org.finos.fluxnova.bpm.engine.impl.cfg.AbstractProcessEnginePlugin;
import org.finos.fluxnova.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;

public final class {name} extends AbstractProcessEnginePlugin {{
  @Override
  public void preInit(ProcessEngineConfigurationImpl configuration) {{
    configuration.setHistoryTimeToLive("{days}");
    configuration.setEnforceHistoryTimeToLive(true);
    System.out.println("BENCHMARK_FIXTURE history_ttl_days={days} enforcement=true");
  }}
}}
'''.encode('utf-8')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(source)
    evidence = output / 'fixture' / (name + '.java')
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(source)
    return {'class': package + '.' + name, 'history_ttl_days': days,
            'enforce_history_ttl': True, 'added_source_path': relative.as_posix(),
            'source_sha256': hashlib.sha256(source).hexdigest(),
            'scope': 'One added test plugin; no tracked upstream source file edited'}


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
    parser.add_argument('--java-major', type=int, choices=(21, 25), default=21,
                        help='Required runtime; Java 25 is an experimental qualification lane')
    parser.add_argument('--maven-repository', help='New isolated Maven repository directory')
    parser.add_argument('--command-timeout-seconds', type=int, default=1800)
    parser.add_argument('--history-ttl-days', type=int,
                        help='Explicit test-fixture default; omit to reproduce upstream configuration')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.sha) or min(args.threads, args.runs, args.command_timeout_seconds) < 1:
        parser.error('A full lowercase SHA and positive counts are required')
    if args.history_ttl_days is not None and not 1 <= args.history_ttl_days <= 36500:
        parser.error('history-ttl-days must be between 1 and 36500')
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
    metadata['required_java_major'] = args.java_major
    metadata['command_timeout_seconds'] = args.command_timeout_seconds
    metadata['limitations'].extend([
        'Selected upstream benchmark profile only; not the full upstream test suite',
        'Java 25 execution does not establish upstream support or production readiness',
        'Different hosted runners do not establish a controlled performance comparison',
    ])
    manifest.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    try:
        for executable in ('java', args.git, args.maven):
            if shutil.which(executable) is None:
                raise RuntimeError('Native suite prerequisite unavailable: ' + executable)
        java = subprocess.run(['java', '-version'], capture_output=True, text=True, check=True, timeout=30)
        metadata['java_version'] = java.stderr.strip() or java.stdout.strip()
        if not re.search(r'version "' + str(args.java_major) + r'[.\"]', metadata['java_version']):
            raise RuntimeError('Runtime does not match the declared Java major')
        metadata['maven_version'] = subprocess.check_output([args.maven, '--version'], text=True, timeout=30).strip()
        metadata['git_version'] = subprocess.check_output([args.git, '--version'], text=True, timeout=30).strip()
        metadata['builder_os'] = platform.platform()
        metadata['builder_reproducibility'] = 'Host tool versions recorded; this invocation does not establish a digest-pinned builder image'
        metadata['dependency_cache'] = 'Existing Maven user repository; dependency cache is not reset'
        run([args.git, 'clone', '--no-checkout', metadata['source'], str(work)], None, log, 300)
        run([args.git, 'checkout', '--detach', args.sha], work, log, 120)
        actual = subprocess.check_output([args.git, 'rev-parse', 'HEAD'], cwd=work, text=True, timeout=30).strip()
        if actual != args.sha:
            raise RuntimeError('Source SHA mismatch')
        settings = ['--settings', str(Path(args.settings).resolve())] if args.settings else []
        if args.database != 'h2' and not args.settings:
            raise RuntimeError('Managed database runs require an explicit external Maven settings file')
        mvn = [args.maven, '--batch-mode', '--no-transfer-progress', *settings]
        if args.history_ttl_days is not None:
            metadata['fixture_extension'] = add_history_fixture(work, output, args.history_ttl_days)
            mvn.append('-DprocessEnginePlugins=' + metadata['fixture_extension']['class'])
            metadata['limitations'].append('Declared history-retention fixture extension; not unmodified upstream test configuration')
        if args.maven_repository:
            repository = Path(args.maven_repository).resolve()
            repository.mkdir(parents=True, exist_ok=False)
            mvn.append('-Dmaven.repo.local=' + str(repository))
            metadata['dependency_cache'] = 'New empty isolated Maven repository'
        metadata['stage'] = 'build_dependencies'
        manifest.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        print('Building pinned upstream dependencies', flush=True)
        run([*mvn, '-Pdistro', '-pl', 'qa/performance-tests-engine', '-am', 'clean', 'install', '-DskipTests'], work, log, args.command_timeout_seconds)
        metadata['stage'] = 'native_benchmark_tests'
        manifest.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        print('Running the selected upstream benchmark profile', flush=True)
        run([*mvn, 'clean', 'install', '-Pbenchmark,' + args.database + ',history-level-' + args.history, '-DnumberOfThreads=' + str(args.threads), '-DnumberOfRuns=' + str(args.runs)], work / 'qa/performance-tests-engine', log, args.command_timeout_seconds)
        retain_results(work, output)
        metadata['test_evidence'] = validate_results(output)
        if args.history_ttl_days is not None:
            marker = f'BENCHMARK_FIXTURE history_ttl_days={args.history_ttl_days} enforcement=true'
            if not any(marker in p.read_text(encoding='utf-8', errors='replace')
                       for p in (output / 'surefire-reports').iterdir() if p.is_file()):
                raise RuntimeError('Missing runtime evidence of the declared history fixture')
            metadata['test_evidence']['history_fixture_runtime_marker_verified'] = True
        metadata['status'] = 'completed'
    except Exception as error:
        metadata['status'] = 'failed'
        metadata['error_type'] = type(error).__name__
        raise
    finally:
        if metadata['status'] != 'completed':
            retain_results(work, output)
        metadata['evidence_sha256'] = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file() and p != manifest}
        metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
        manifest.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
