#!/usr/bin/env python3
"""Independently audit a deliberately failed FNV-06 timeout-capture trial."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.audit_fnv06_load_qualification import audit

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--control',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or any(a.output.resolve().is_relative_to(x.resolve()) for x in (a.source,a.control)):
        p.error('Use a fresh audit output outside both sealed evidence directories.')
    result=audit(a.source,timeout_control=a.control)
    a.output.mkdir(parents=True);(a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='diagnostics'}))

if __name__=='__main__':main()
