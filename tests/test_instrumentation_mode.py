import unittest
from scripts.instrumentation_mode import agent_environment,AGENT


class InstrumentationModeTests(unittest.TestCase):
    def test_toggle_preserves_every_other_environment_value(self):
        original={'JAVA_TOOL_OPTIONS':AGENT,'JAVA_OPTS':'-Xms4g -Xmx4g','OTEL_TRACES_SAMPLER_ARG':'0.1','PASSWORD':'example'}
        off=agent_environment(original,False)
        self.assertEqual(off,{k:v for k,v in original.items() if k!='JAVA_TOOL_OPTIONS'})
        self.assertEqual(agent_environment(off,True),original)
        self.assertEqual(original['JAVA_TOOL_OPTIONS'],AGENT)

    def test_other_jvm_options_are_not_silently_discarded(self):
        for enabled in (True,False):
            with self.assertRaises(ValueError):agent_environment({'JAVA_TOOL_OPTIONS':AGENT+' -Dother=value'},enabled)


if __name__=='__main__':unittest.main()
