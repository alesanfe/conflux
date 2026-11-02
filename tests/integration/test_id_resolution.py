# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Integración: resolución de ids — _resolve_sid + _resolve_meta + _abs_ep."""
import unittest

from tests import patch_fetch, px


class TestIdResolutionChain(unittest.TestCase):
    def setUp(self):
        px._idmap.clear()

    def tearDown(self):
        px._idmap.clear()

    def test_kitsu_abs_ep_a_tt_SE(self):
        """Cadena completa: kitsu:49847:2 -> meta -> imdb_id + videos -> tt:1:2."""
        meta = {'meta': {'imdb_id': 'tt2359704', 'videos': [
            {'id': 'kitsu:49847:1', 'season': '1', 'episode': '1'},
            {'id': 'kitsu:49847:2', 'season': '1', 'episode': '2'}]}}
        restore = patch_fetch(lambda *a, **k: meta)
        try:
            self.assertEqual(px._resolve_sid('series', 'kitsu:49847:2'),
                             'tt2359704:1:2')
        finally:
            restore()

    def test_upstream_sin_imdb_devuelve_original(self):
        restore = patch_fetch(lambda *a, **k: {'meta': {'videos': []}})
        try:
            sid = px._resolve_sid('series', 'kitsu:1:1')
            self.assertTrue(sid.startswith('kitsu'))
        finally:
            restore()

    def test_upstream_caído_no_rompe(self):
        restore = patch_fetch(lambda *a, **k: None)
        try:
            sid = px._resolve_sid('series', 'kitsu:1:1')
            self.assertTrue(sid.startswith('kitsu'))
        finally:
            restore()


if __name__ == '__main__':
    unittest.main()
