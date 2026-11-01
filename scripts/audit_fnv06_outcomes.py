#!/usr/bin/env python3
"""Offline audit of complete FNV-06 outcomes without hiding request failures."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.audit_fnv06_load_qualification import audit

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or a.output.resolve().is_relative_to(a.source.resolve()):p.error('Use a fresh audit output outside sealed evidence.')
    try:result=audit(a.source,outcome_accounting=True)
    except (AssertionError,KeyError,ValueError,TypeError,FileNotFoundError,IndexError) as error:
        result={'study_id':'FNV-06-OUTCOME-ACCOUNTING','status':'not_verified','independent_outcomes_verified':False,
            'reset_eligible':False,'verified_correct_completions':None,'error_type':type(error).__name__,
            'scope':'Evidence did not meet the complete-outcome audit. No missing or incorrect outcome is promoted to a pass.'}
        a.output.mkdir(parents=True);(a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result));raise SystemExit(1)
    a.output.mkdir(parents=True);(a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='diagnostics'}))

if __name__=='__main__':main()
