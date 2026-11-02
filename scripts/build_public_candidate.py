#!/usr/bin/env python3
"""Build a fail-closed public snapshot using an explicit final-document inventory."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'release/public-files.json'
CORE = {'benchlib', 'configs', 'deploy', 'infra', 'load', 'providers', 'schemas', 'scripts',
        'services', 'telemetry', 'tests', 'workloads', '.github'}
ROOT_FILES = {'README.md', 'LICENSE', 'pyproject.toml', 'requirements-cloud.txt',
              'requirements-database.txt', 'bench.ps1', 'bench.sh', '.gitignore',
              '.gitattributes', '.dockerignore', '.gitleaksignore', '.gitleaks.toml',
              'CONTRIBUTING.md', 'SECURITY.md', 'CITATION.cff', 'release/public-files.json',
              'telemetry/requirements.txt'}
# HTML in workloads/services is application UI code, including deployed BPMN
# review forms. Documents outside CORE still require an exact inventory entry.
DOCUMENT_SUFFIXES = {'.md', '.pdf', '.pptx', '.ppt', '.docx', '.doc', '.xlsx',
                     '.xls', '.rtf', '.odt', '.ods', '.odp', '.txt'}
IGNORE_BEGIN = '# BEGIN final-document inventory (release/public-files.json)'
IGNORE_END = '# END final-document inventory'
SENSITIVE_TEXT = {
    'internal_classification': re.compile(r'Oracle\s+Restricted|Employees\s+Only', re.I),
    'workstation_user_path': re.compile(r'[A-Z]:[\\/]+Users[\\/]+[^\\/\s"<>]+[\\/]', re.I),
    'deployed_oci_identifier': re.compile(r'ocid1\.[a-z0-9]+\.[a-z0-9]+\.[a-z0-9-]*\.[a-z0-9]{25,}', re.I),
    'private_registry_namespace': re.compile(r'ocir\.[a-z0-9-]+\.oci\.oraclecloud\.com/[a-zA-Z0-9_.-]+/'),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_relative(name):
    path = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or any(c in name for c in '\0\r\n') or path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe release path')
    return name


def load_policy(path=POLICY):
    policy = json.loads(path.read_text(encoding='utf-8'))
    if policy['schema_version'] != 1 or policy['license'] != 'Apache-2.0':
        raise ValueError('Unsupported release policy')
    names = policy['documents']
    if len(names) != len(set(names)):
        raise ValueError('Duplicate document entry')
    for name in names + policy['evidence_directories']:
        safe_relative(name)
    for name, digest in policy['reviewed_assets'].items():
        if name not in names or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Unreviewed asset entry')
    return policy


def included(name, policy):
    safe_relative(name)
    path = PurePosixPath(name)
    if name in ROOT_FILES or name in policy['documents']:
        return True
    if path.parent.as_posix() in policy['evidence_directories']:
        return path.name in policy['evidence_filenames']
    if path.suffix.lower() in DOCUMENT_SUFFIXES:
        return False
    return path.parts[0] in CORE


def ignore_block(policy):
    # Exact reviewed files; reopen parents so Git can traverse directories.
    lines = [IGNORE_BEGIN, '/docs/**', '/presentations/**', '/drafts/', '/notes/',
             '/archive/', '*.pptx', '*.ppt', '*.docx', '*.doc', '*.xlsx', '*.pdf']
    parents = set()
    for name in policy['documents']:
        path = PurePosixPath(name)
        parents.update(p.as_posix() for p in path.parents if str(p) != '.')
    lines.extend('!/' + p + '/' for p in sorted(parents, key=lambda p: (p.count('/'), p)))
    lines.extend('!/' + p for p in sorted(policy['documents']))
    return '\n'.join([*lines, IGNORE_END])


def sync_ignore(policy, path=ROOT / '.gitignore'):
    current = path.read_text(encoding='utf-8')
    block = ignore_block(policy)
    pattern = re.escape(IGNORE_BEGIN) + r'.*?' + re.escape(IGNORE_END)
    if IGNORE_BEGIN in current:
        if IGNORE_END not in current:
            raise ValueError('Incomplete managed ignore block')
        current = re.sub(pattern, lambda _: block, current, flags=re.S)
    else:
        current = current.rstrip() + '\n\n' + block + '\n'
    path.write_text(current, encoding='utf-8')


def sanitize_markdown(data):
    value = data.decode('utf-8')
    return SENSITIVE_TEXT['private_registry_namespace'].sub(
        'REGISTRY_HOST/REGISTRY_NAMESPACE/', value).encode('utf-8')


def check_content(name, data, policy):
    path = PurePosixPath(name)
    if path.suffix.lower() in {'.pem', '.key', '.tfstate', '.tfplan'} or '.local.' in path.name:
        raise ValueError('Private file in release: ' + name)
    if name in policy['reviewed_assets']:
        if sha(data) != policy['reviewed_assets'][name]:
            raise ValueError('Reviewed asset changed: ' + name)
        return
    try:
        value = data.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('Binary asset lacks explicit review hash: ' + name) from None
    for reason, pattern in SENSITIVE_TEXT.items():
        if pattern.search(value):
            raise ValueError(reason + ': ' + name)


def committed_bytes(names, git, cwd):
    """Read canonical committed bytes, independent of checkout line endings."""
    for name in names:
        safe_relative(name)
    request = ''.join('HEAD:' + name + '\n' for name in names).encode('utf-8')
    result = subprocess.run([*git, 'cat-file', '--batch'], input=request, cwd=cwd,
                            capture_output=True, check=True, timeout=60)
    data = result.stdout
    cursor = 0
    files = {}
    for name in names:
        end = data.index(b'\n', cursor)
        header = data[cursor:end].split()
        if len(header) != 3 or header[1] != b'blob':
            raise ValueError('Missing committed source: ' + name)
        size = int(header[2])
        cursor = end + 1
        value = data[cursor:cursor + size]
        cursor += size
        if len(value) != size or data[cursor:cursor + 1] != b'\n':
            raise ValueError('Incomplete committed source: ' + name)
        files[name] = value
        cursor += 1
    if cursor != len(data):
        raise ValueError('Unexpected source inventory')
    return files


def build(output):
    output = output.resolve()
    archive = output.with_suffix('.zip')
    if output.exists() or archive.exists() or not output.is_relative_to(ROOT / 'dist'):
        raise ValueError('Use a fresh directory and ZIP under dist/')
    policy = load_policy()
    git = ['git', '-c', 'safe.directory=' + ROOT.as_posix()]
    if subprocess.check_output([*git, 'status', '--porcelain'], cwd=ROOT):
        raise ValueError('Commit the reviewed sources before exporting')
    source_commit = subprocess.check_output([*git, 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    tracked = subprocess.check_output([*git, 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    missing = set(policy['documents']) - set(tracked)
    if missing:
        raise ValueError('Final document is not tracked: ' + ', '.join(sorted(missing)))
    chosen = [n for n in tracked if n and included(n, policy)]
    committed = committed_bytes(chosen, git, ROOT)
    output.mkdir(parents=True)
    records = []
    for name in chosen:
        source = ROOT / name
        if not source.is_file() or source.is_symlink():
            raise ValueError('Invalid source file: ' + name)
        data = committed[name]
        original = sha(data)
        if name == '.gitleaksignore':
            data = b'# No private-history exceptions are inherited by the public kit.\n# Exact safe example values are scoped in .gitleaks.toml.\n'
        if source.suffix == '.md':
            data = sanitize_markdown(data)
        check_content(name, data, policy)
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append({'path': name, 'source_sha256': original, 'candidate_sha256': sha(data)})
    archive_refs = []
    for path in output.rglob('*.md'):
        value = path.read_text(encoding='utf-8')
        def link(match):
            label, target = match.groups()
            target = target.strip('<>')
            if re.match(r'^[a-z]+:', target) or target.startswith('#'):
                return match.group(0)
            target_path = target.split('#', 1)[0]
            if not target_path or (path.parent / target_path).resolve().exists():
                return match.group(0)
            archive_refs.append({'file': path.relative_to(output).as_posix(), 'target': target})
            return label + ' (retained in the private research archive)'
        path.write_text(re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, value), encoding='utf-8', newline='\n')
    for row in records:
        row['candidate_sha256'] = sha((output / row['path']).read_bytes())
    manifest = {'status': 'source_snapshot', 'source_commit': source_commit,
        'contains_git_history': False, 'license': policy['license'], 'destination': policy['destination'],
        'publication_status': policy['publication_status'], 'files': records,
        'excluded_file_count': len([n for n in tracked if n and n not in chosen]),
        'archive_references_made_explicit': archive_refs, 'raw_private_cloud_evidence_included': False}
    (output / 'RELEASE-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline='\n')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(output).as_posix())
    return {'folder': str(output), 'archive': str(archive), 'files': len(records) + 1,
        'sha256': sha(archive.read_bytes()), 'source_commit': source_commit,
        'publication_status': policy['publication_status']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--output', type=Path)
    group.add_argument('--sync-ignore', action='store_true')
    args = parser.parse_args()
    if args.sync_ignore:
        sync_ignore(load_policy())
        print('Updated final-document ignore rules.')
    else:
        print(json.dumps(build(args.output), indent=2))


if __name__ == '__main__':
    main()
