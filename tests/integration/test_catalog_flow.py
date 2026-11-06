# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Integración: handle_catalog — routing completo upstream + caché."""
import time
import unittest

import conflux.core.state as st
import conflux.domain.catalog as C
from tests import patch_fetch


class TestCatalogFlow(unittest.TestCase):
    def setUp(self):
        C._cat_c.clear()

    def tearDown(self):
        C._cat_c.clear()

    def test_catalog_upstream_metas(self):
        d_cat = {'metas': [{'id': 'tt1', 'name': 'X'}]}
        restore = patch_fetch(lambda *a, **k: d_cat)
        try:
            d = C.handle_catalog('movie', 'scinemeta_movie_top', None)
            self.assertEqual(d['metas'][0]['id'], 'tt1')
        finally:
            restore()

    def test_cache_hit_segunda_vez_sin_fetch(self):
        calls = []
        def fake(url, t=7):
            calls.append(url)
            return {'metas': [{'id': 'tt2'}]}
        restore = patch_fetch(fake)
        try:
            C.handle_catalog('movie', 'scinemeta_movie_top', None)
            C.handle_catalog('movie', 'scinemeta_movie_top', None)
            self.assertEqual(len(calls), 1, 'segunda llamada sirve caché')
        finally:
            restore()

    def test_cid_malo_no_explota(self):
        d = C.handle_catalog('movie', '%%%', None)
        self.assertEqual(d, {'metas': []})

    def test_upstream_muerto_stale(self):
        key = ('movie', 'scinemeta_movie_top', None)
        # ts: expirado para _cget (>CACHE_TTL) pero dentro de ventana stale (<24h)
        C._cat_c[key] = (time.time() - st.CACHE_TTL - 60, {'metas': [{'id': 'viejo'}]})
        restore = patch_fetch(lambda *a, **k: None)
        try:
            d = C.handle_catalog('movie', 'scinemeta_movie_top', None)
            self.assertEqual(d['metas'][0]['id'], 'viejo')
        finally:
            restore()

    def test_poster_muerto_se_sirve_placeholder_sin_ensuciar_cache(self):
        """Wiring handle_catalog -> _posters_view: el cliente recibe el
        fallback para posters 404, pero el objeto cacheado conserva la url
        original (resurrección posible a los 30min)."""
        import conflux.domain.metafix as mf
        dead = 'https://images.metahub.space/poster/small/tt9/img'
        old = (dict(mf._POSTER_BAD), dict(mf._POSTER_OK))
        mf._POSTER_BAD.clear(); mf._POSTER_BAD[dead] = (2, st.CLOCK.time())
        restore = patch_fetch(lambda *a, **k: {'metas': [
            {'id': 'tt9', 'poster': dead}, {'id': 'tt8', 'poster': 'http://ok/1'}]})
        try:
            from unittest.mock import patch
            with patch.object(mf, '_POOL_BG'):
                d1 = C.handle_catalog('movie', 'scinemeta_movie_top', None)
                d2 = C.handle_catalog('movie', 'scinemeta_movie_top', None)  # cache hit
            self.assertEqual(d1['metas'][0]['poster'], mf._POSTER_FB)
            self.assertEqual(d2['metas'][0]['poster'], mf._POSTER_FB)
            self.assertEqual(d1['metas'][1]['poster'], 'http://ok/1')
            key = ('movie', 'scinemeta_movie_top', None)
            self.assertEqual(C._cat_c[key][1]['metas'][0]['poster'], dead)
        finally:
            restore()
            mf._POSTER_BAD.clear(); mf._POSTER_BAD.update(old[0])
            mf._POSTER_OK.clear(); mf._POSTER_OK.update(old[1])


if __name__ == '__main__':
    unittest.main()
