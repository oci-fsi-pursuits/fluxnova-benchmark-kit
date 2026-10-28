"""Build support/worker container using a digest-pinned Python base image."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-image', required=True, help='Python3.12+ image@sha256:digest')
    parser.add_argument('--tag', required=True, help='Local build tag; pushing is a separate deployment step')
    parser.add_argument('--output', required=True, help='New evidence JSON path')
    args = parser.parse_args()
    if not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', args.python_image):
        parser.error('python-image must be pinned by sha256 digest')
    path = Path(args.output)
    if path.exists():
        parser.error('output already exists')
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['docker', 'build', '--build-arg', 'PYTHON_IMAGE=' + args.python_image, '--file', str(root / 'services/Dockerfile'), '--tag', args.tag, str(root)], check=True)
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.tag], text=True))[0]
    evidence = {'base_image': args.python_image, 'image_id': image['Id'], 'repo_digests': image.get('RepoDigests', []), 'tag': args.tag, 'services_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'services').glob('*.py')}, 'publication': 'local build only; record registry digest after push'}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
