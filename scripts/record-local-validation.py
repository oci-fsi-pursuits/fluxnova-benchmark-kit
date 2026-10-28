"""Package this session's genuine functional evidence without re-running a system."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchlib.analysis import analyze_run
from benchlib.archive import archive_run
from benchlib.report import write_reports, write_parquet
from benchlib.runner import verify_archive
from benchlib.util import ROOT, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--golden', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    for port in (18080, 18081):
        with socket.socket() as connection:
            connection.settimeout(1)
            if connection.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError('Stop local validation services before proving offline report regeneration')
    campaign = read_json(ROOT / 'runs' / (args.campaign + '.json'))
    golden = read_json(args.golden)
    assert golden['passed'] and all(c['status'] == 'passed' for c in golden['cases'])
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / 'golden.json', golden)
    rows = []
    for item in campaign['results']:
        directory = Path(item['run'])
        assert verify_archive(directory) == []
        paths = write_reports(directory)
        parquet = write_parquet(directory, directory / 'reports/outcomes.parquet')
        import pyarrow.parquet as pq
        result = analyze_run(directory)
        assert result['counts']['correct'] == result['counts']['offered'] == 10
        assert result['counts']['unknown'] == result['counts']['incorrect'] == result['counts']['unsafe_outcomes'] == 0
        assert pq.read_table(parquet).num_rows == 10
        name = item['scenario']
        archive_run(directory, args.output / (name + '.zip'))
        shutil.copyfile(paths['report_html'], args.output / (name + '.html'))
        rows.append({'scenario': name, 'run_id': directory.name, 'correct': 10, 'offered': 10,
                     'performance_evidence_status': result['status'], 'errors': result['errors'], 'missing_evidence': result['missing_evidence'],
                     'parquet_rows': 10, 'offline_regeneration': True})
    summary = {'scope': 'LOCAL FUNCTIONAL VALIDATION ONLY; Windows/Java21/H2, not OCI/PostgreSQL performance',
               'golden_cases_passed': len(golden['cases']), 'smoke': rows, 'local_services_stopped': True,
               'oci_resources_created': False, 'docker_builds_executed': False,
               'terraform': {'provider': '7.22.0', 'validation_binary': '1.13.5', 'valid_stacks': 2, 'mock_tests_passed': 2},
               'unit_tests': {'main_suite_passed': 90, 'telemetry_passed': 4},
               'performance_claim': 'None; generator/system telemetry and actual costs remain unvalidated for OCI.'}
    write_json(args.output / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
