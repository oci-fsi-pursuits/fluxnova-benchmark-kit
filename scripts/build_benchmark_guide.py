#!/usr/bin/env python3
"""Regenerate the checked-in catalogue guide, or check it for drift."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchlib.catalog import load_catalog, validate_catalog, markdown, html_guide


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    data = load_catalog()
    validate_catalog(data)
    for name, content in [('benchmark-guide.md', markdown(data)), ('benchmark-guide.html', html_guide(data))]:
        path = ROOT / 'docs' / name
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                raise SystemExit('Generated guide is stale: ' + name)
        else:
            path.write_text(content, encoding='utf-8', newline='\n')
    print('Benchmark catalogue and guides are consistent.')


if __name__ == '__main__':
    main()
