#!/usr/bin/env python3
"""Audit a sealed local FNV-07 timing qualification; no service mutations."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.fnv07_timing_audit import audit

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.folder), indent=2))
