# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.wikidata — claims parsing."""
import unittest

import conflux.infra.wikidata as W


class TestPv(unittest.TestCase):
    def test_primer_valor(self):
        claims = {'P17': [{'mainsnak': {'datavalue': {'value': 'es'}}}]}
        self.assertEqual(W._pv(claims, 'P17'), 'es')

    def test_sin_claim_none(self):
        self.assertIsNone(W._pv({}, 'P17'))


if __name__ == '__main__':
    unittest.main()
