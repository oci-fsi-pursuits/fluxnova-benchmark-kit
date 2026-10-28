import copy
import unittest

from benchlib.w02_mixed_cloud import summarize_mixed_pairs
from scripts.render_w02_mixed_cloud import render_report
import test_w02_mixed_cloud as cloud_fixtures


class MixedCloudViewTests(unittest.TestCase):
    def fixture(self):
        fixtures=cloud_fixtures.MixedCloudTests();plan,_=fixtures.plan()
        result=summarize_mixed_pairs(plan,fixtures.records(plan))
        result.update(phase='campaign',measurement_outcomes={'settled':2400,'rejected':2400,'blocked':1200},
            persisted_store_audit=[],export_files_verified=100)
        return result

    def test_settlement_gain_keeps_refusal_regression_visible(self):
        html=render_report(self.fixture())
        self.assertIn('+0.0500 timely settlements/s',html)
        self.assertEqual(html.count('class="regression"'),5)
        self.assertEqual(html.count('-12.5 pp'),5)
        self.assertIn('Expected refusals',html)
        self.assertIn('medians of run percentiles, not pooled request percentiles',html)

    def test_partial_or_qualification_output_cannot_show_stale_aggregate(self):
        for status,phase,count in [('incomplete_or_invalid','campaign',9),('passed','qualification',4),('complete','campaign',9)]:
            result=copy.deepcopy(self.fixture());result.update(status=status,phase=phase,completed_arms=count)
            html=render_report(result)
            self.assertIn('No aggregate improvement claim from this phase.',html)
            self.assertNotIn('+0.0500 timely settlements/s',html)
            self.assertNotIn('id="outcomes"',html)

    def test_complete_label_requires_all_five_pairs(self):
        result=self.fixture();result['comparison']['paired_runs'].pop()
        with self.assertRaisesRegex(ValueError,'Five pairs'):render_report(result)
        result=self.fixture();result['comparison']=None
        with self.assertRaisesRegex(ValueError,'no audited comparison'):render_report(result)


if __name__=='__main__':unittest.main()
