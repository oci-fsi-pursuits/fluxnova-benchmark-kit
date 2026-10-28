import unittest
from benchlib.network_binding import require_endpoint_bindings


class NetworkBindingTests(unittest.TestCase):
    def test_frozen_endpoint_must_still_identify_both_services_after_reset(self):
        expected = {'engine': 'http://172.28.3.20:8080/engine-rest',
                    'ledger': 'http://172.28.3.10:8090'}
        require_endpoint_bindings(expected, dict(expected))
        for actual in [dict(expected, engine='http://172.28.3.10:8080/engine-rest'),
                       dict(expected, ledger='http://172.28.3.21:8090'), {},
                       {'engine': expected['engine']}]:
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                require_endpoint_bindings(expected, actual)
