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
