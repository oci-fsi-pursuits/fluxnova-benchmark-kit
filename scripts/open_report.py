#!/usr/bin/env python3
"""Open a saved HTML or PDF report in the operating system's default viewer."""
import argparse
import os
from pathlib import Path
import webbrowser


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path',type=Path,required=True)
    parser.add_argument('--print-only',action='store_true')
    args=parser.parse_args();path=args.path.expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in ('.html','.pdf'):
        parser.error('Choose an existing .html or .pdf report.')
    print(path);print(path.as_uri())
    if not args.print_only:
        if os.name=='nt':os.startfile(str(path))
        elif not webbrowser.open(path.as_uri()):parser.error('No viewer is available; open the printed path manually.')


if __name__=='__main__':main()
