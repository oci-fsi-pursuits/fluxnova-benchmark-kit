"""Export only reviewed static walkthrough assets; never package lab/runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ('.nojekyll', 'index.html', 'app.js', 'style.css')


def build(output):
    output = Path(output).resolve()
    source = ROOT / 'site'
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Use a fresh output outside site/')
    if subprocess.check_output(['git', 'status', '--porcelain', '--', 'site'], cwd=ROOT):
        raise ValueError('Commit reviewed site assets before publishing')
    if {p.name for p in source.iterdir()} != set(ASSETS):
        raise ValueError('Static site contains an unreviewed asset')
    output.mkdir(parents=True)
    hashes = {}
    for name in ASSETS:
        path = source / name
        if path.is_symlink() or not path.is_file(): raise ValueError('Static assets must be regular files')
        data = path.read_bytes(); (output / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    result = {'source_commit': commit, 'assets': hashes,
              'scope': 'Static synthetic illustration. No engine, backend, data submission or measured performance.'}
    (output / 'build.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2))
