"""Reconcile saved standalone HTTP probe evidence without running services."""
import argparse
import json
from pathlib import Path
from run_fnv07_http_probe import audit_probe

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_probe(args.folder), indent=2))
