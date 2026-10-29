import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import xml.etree.ElementTree as ET

from scripts.build_native import (add_history_fixture, contains_marker, main,
                                 repair_legacy_feel_fixtures, retain_results, validate_results)


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name) / 'evidence'
        for name in ('surefire-reports', 'results', 'reports'):
            (self.output / name).mkdir(parents=True)
        self.xml = self.output / 'surefire-reports/TEST-sequence.xml'
        self.xml.write_text('<testsuite><testcase name="sequence"/></testsuite>')
        self.raw = self.output / 'results/sequence.json'
        self.raw.write_text(json.dumps({'testName': 'sequence',
                                       'passResults': [{'duration': 10, 'numberOfThreads': 1}]}))
        (self.output / 'reports/index.html').write_text('<html>Report</html>')

    def test_real_testcases_and_result_files_are_required(self):
        result = validate_results(self.output)
        self.assertEqual(result['junit']['tests'], 1)
        self.assertEqual(result['native_result_files'], 1)
        self.xml.write_text('<testsuite tests="100"/>')
        with self.assertRaisesRegex(RuntimeError, 'tests missing'):
            validate_results(self.output)

    def test_failed_errored_and_all_skipped_runs_cannot_qualify(self):
        for marker in ('failure', 'error', 'skipped'):
            with self.subTest(marker=marker):
                self.xml.write_text(f'<testsuite><testcase name="sequence"><{marker}/></testcase></testsuite>')
                with self.assertRaisesRegex(RuntimeError, 'unsuccessful'):
                    validate_results(self.output)

    def test_empty_or_missing_native_results_cannot_qualify(self):
        self.raw.write_text('{"testName":"sequence","passResults":[]}')
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            validate_results(self.output)
        self.raw.unlink()
        with self.assertRaisesRegex(RuntimeError, 'Missing native'):
            validate_results(self.output)

    def test_partial_failure_evidence_is_retained_idempotently(self):
        work = Path(self.folder.name) / 'source'
        target = work / 'qa/performance-tests-engine/target/surefire-reports'
        target.mkdir(parents=True)
        failed_xml = '<testsuite><testcase name="failed"><failure/></testcase></testsuite>'
        (target / 'TEST-failed.xml').write_text(failed_xml)
        dest = Path(self.folder.name) / 'failed-attempt'
        retain_results(work, dest)
        retain_results(work, dest)
        self.assertEqual((dest / 'surefire-reports/TEST-failed.xml').read_text(), failed_xml)
        with self.assertRaisesRegex(RuntimeError, 'unsuccessful'):
            validate_results(dest)

    def test_wrong_java_major_is_recorded_as_failed_before_checkout(self):
        output = Path(self.folder.name) / 'wrong-runtime'
        argv = ['build_native.py', '--sha', 'a' * 40, '--java-major', '25',
                '--workspace', str(Path(self.folder.name) / 'work'),
                '--output-dir', str(output)]
        version = subprocess.CompletedProcess(['java', '-version'], 0, '', 'openjdk version "21.0.12.1"')
        with patch('sys.argv', argv), patch('shutil.which', return_value='found'), \
                patch('subprocess.run', return_value=version), \
                patch('scripts.build_native.run') as execute:
            with self.assertRaisesRegex(RuntimeError, 'declared Java'):
                main()
        execute.assert_not_called()
        receipt = json.loads((output / 'native-build.json').read_text())
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['required_java_major'], 25)

    def test_failed_native_command_keeps_manifest_and_test_failure(self):
        output = Path(self.folder.name) / 'failed-command'
        workspace = Path(self.folder.name) / 'work'
        upstream = 'b' * 40
        argv = ['build_native.py', '--sha', upstream, '--workspace', str(workspace),
                '--output-dir', str(output)]
        version = subprocess.CompletedProcess(['java', '-version'], 0, '', 'openjdk version "21.0.12.1"')

        def execute(command, cwd, log, timeout):
            log.write_text('retained command log')
            if '-Pbenchmark,h2,history-level-full' in command:
                target = workspace / upstream / 'qa/performance-tests-engine/target/surefire-reports'
                target.mkdir(parents=True)
                (target / 'TEST-failed.xml').write_text('<testsuite><testcase name="failed"><error/></testcase></testsuite>')
                raise subprocess.CalledProcessError(1, command)

        with patch('sys.argv', argv), patch('shutil.which', return_value='found'), \
                patch('subprocess.run', return_value=version), \
                patch('subprocess.check_output', side_effect=['Maven test', 'Git test', upstream]), \
                patch('platform.platform', return_value='isolated-test-host'), \
                patch('scripts.build_native.run', side_effect=execute):
            with self.assertRaises(subprocess.CalledProcessError):
                main()
        receipt = json.loads((output / 'native-build.json').read_text())
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['stage'], 'native_benchmark_tests')
        self.assertIn('surefire-reports/TEST-failed.xml', receipt['evidence_sha256'])
        self.assertIn('native-build.log', receipt['evidence_sha256'])

    def test_retention_fixture_records_exact_source_without_replacing_upstream(self):
        work = Path(self.folder.name) / 'source'
        fixture = add_history_fixture(work, self.output, 30)
        source = work / fixture['added_source_path']
        retained = self.output / 'fixture/BenchmarkHistoryRetentionPlugin.java'
        self.assertEqual(source.read_bytes(), retained.read_bytes())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), fixture['source_sha256'])
        before = source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'must not replace'):
            add_history_fixture(work, self.output, 60)
        self.assertEqual(source.read_bytes(), before)

    def test_invalid_retention_cannot_create_a_fixture(self):
        work = Path(self.folder.name) / 'invalid-retention'
        for days in (0, -1, 36501, True, '30'):
            with self.subTest(days=days), self.assertRaises(ValueError):
                add_history_fixture(work, self.output, days)
        self.assertFalse(work.exists())

    def make_dmn_fixtures(self):
        work = Path(self.folder.name) / 'dmn-source'
        root = work / 'qa/performance-tests-engine/src/test/resources/org/finos/fluxnova/bpm/qa/performance/engine/dmn'
        root.mkdir(parents=True)
        for suffix in ('fiveRules', 'fiveRulesTwoInputs', 'oneHundredRules', 'oneHundredRulesTwoInputs',
                       'tenRules', 'tenRulesTwoInputs', 'twoRules', 'twoRulesTwoInputs'):
            whitespace = '\n' if 'TwoInputs' in suffix else ' '
            (root / f'DmnEnginePerformanceTest.{suffix}.dmn').write_text(
                f'<definitions expressionLanguage="http://www.omg{whitespace}.org/spec/FEEL/20140401">'
                '<decision id="kept"><inputExpression>amount &gt; 100</inputExpression></decision></definitions>')
        return work, root

    def test_declared_feel_repair_preserves_decision_rules_and_retains_originals(self):
        work, root = self.make_dmn_fixtures()
        before = {p.name: p.read_bytes() for p in root.iterdir()}
        receipt = repair_legacy_feel_fixtures(work, self.output)
        self.assertEqual(len(receipt['files']), 8)
        for path in root.iterdir():
            original = ET.fromstring(before[path.name])
            fixed = ET.fromstring(path.read_bytes())
            self.assertEqual(fixed.get('expressionLanguage'), 'http://www.omg.org/spec/FEEL/20140401')
            original.attrib.pop('expressionLanguage'); fixed.attrib.pop('expressionLanguage')
            self.assertEqual(ET.tostring(original), ET.tostring(fixed))
            self.assertEqual((self.output / 'fixture/legacy-feel-uri' / (path.name + '.before')).read_bytes(), before[path.name])
        self.assertEqual(repair_legacy_feel_fixtures(work, self.output)['status'], 'not_needed')

    def test_unexpected_feel_fixture_fails_before_any_model_is_changed(self):
        work, root = self.make_dmn_fixtures()
        (root / 'unexpected.dmn').write_bytes(next(root.glob('*.dmn')).read_bytes())
        before = {p.name: p.read_bytes() for p in root.iterdir()}
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            repair_legacy_feel_fixtures(work, self.output)
        self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_runtime_marker_is_found_across_stream_chunks(self):
        marker = 'BENCHMARK_FIXTURE history_ttl_days=30 enforcement=true'
        path = Path(self.folder.name) / 'streamed.log'
        path.write_bytes(b'x' * 65525 + marker.encode() + b'x' * 65536)
        self.assertTrue(contains_marker(path, marker))
        self.assertFalse(contains_marker(path, marker + ' missing'))


if __name__ == '__main__':
    unittest.main()
