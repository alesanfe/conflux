# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.trakt — seeds, related."""
import unittest

import conflux.infra.trakt as T


class TestRecomSeeds(unittest.TestCase):
    def test_extrae_slugs(self):
        hist = [{'movie': {'ids': {'trakt': 'dune-2021'}}},
                {'show': {'ids': {'trakt': 'breaking-bad'}}},
                {'movie': {'ids': {}}}]
        self.assertEqual(T._recom_seeds(hist), ['dune-2021', 'breaking-bad'])


if __name__ == '__main__':
    unittest.main()
