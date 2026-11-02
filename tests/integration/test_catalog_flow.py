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


if __name__ == '__main__':
    unittest.main()
