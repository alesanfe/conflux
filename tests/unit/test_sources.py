# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.sources — recursos, resolución de ids."""
import unittest

import conflux.domain.sources as _sources
import conflux.infra.net as _net
from tests import px


class TestInfra(unittest.TestCase):
    def test_res_of_name_o_id(self):
        self.assertIn('stream', px.res_of({'resources': [{'id': 'stream'}]}))
        self.assertIn('stream', px.res_of({'resources': [{'name': 'stream'}]}))
        self.assertIn('stream', px.res_of({'resources': ['stream']}))

    def test_res_types(self):
        m = {'resources': [{'name': 'stream', 'types': ['movie']}]}
        self.assertEqual(px.res_types(m, 'stream'), {'movie'})
        self.assertIsNone(px.res_types(m, 'meta'))


class TestResolveSid(unittest.TestCase):
    def setUp(self):
        px._idmap.clear()

    def tearDown(self):
        px._idmap.clear()

    def test_episodio_absoluto_a_SE(self):
        """kitsu:49847:2 (ep absoluto) -> tt...:1:2 vía videos del meta."""
        meta = {'meta': {'imdb_id': 'tt2359704', 'videos': [
            {'id': 'kitsu:49847:1', 'season': '1', 'episode': '1'},
            {'id': 'kitsu:49847:2', 'season': '1', 'episode': '2'}]}}
        orig = _sources.fetch
        _sources.fetch = _net.fetch = lambda *a, **k: meta
        try:
            self.assertEqual(px._resolve_sid('series', 'kitsu:49847:2'), 'tt2359704:1:2')
            self.assertEqual(px._resolve_sid('series', 'kitsu:7442:1:1'), 'tt2359704:1:1')
        finally:
            _sources.fetch = _net.fetch = orig

    def test_tt_no_se_traduce(self):
        self.assertEqual(px._resolve_sid('series', 'tt0944947:1:1'), 'tt0944947:1:1')

    def test_cache_idmap_no_repite_fetch(self):
        meta = {'meta': {'imdb_id': 'tt1', 'videos': []}}
        calls = []
        orig = _sources.fetch
        _sources.fetch = _net.fetch = lambda *a, **k: (calls.append(a), meta)[1]
        try:
            px._resolve_sid('series', 'kitsu:49847:2')
            px._resolve_sid('series', 'kitsu:49847:2')
            self.assertEqual(len(calls), 1)
        finally:
            _sources.fetch = _net.fetch = orig


if __name__ == '__main__':
    unittest.main()
