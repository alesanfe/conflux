"""Integración: handle_meta — race multi-fuente + fix_metas."""
import unittest

import conflux.app.meta as M
import conflux.core.state as st
from test import patch_fetch


class TestMetaFlow(unittest.TestCase):
    def setUp(self):
        st._meta_c.clear()
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m
        st._meta_c.clear()

    def test_fuente_con_meta_devuelve_meta(self):
        st.manifests[:] = [{'resources': [{'name': 'meta', 'types': ['movie']}]}]
        d_meta = {'meta': {'id': 'tt1', 'name': 'Película'}}
        restore = patch_fetch(lambda *a, **k: d_meta)
        try:
            d = M.handle_meta('movie', 'tt1')
            self.assertEqual(d['meta']['name'], 'Película')
        finally:
            restore()

    def test_sin_fuentes_meta_vacio(self):
        st.manifests[:] = []
        restore = patch_fetch(lambda *a, **k: None)
        try:
            d = M.handle_meta('movie', 'tt1')
            self.assertEqual(d, {'meta': {}})
        finally:
            restore()

    def test_series_cae_a_movie(self):
        """series sin meta upstream -> retry como movie (estrenos calendario)."""
        st.manifests[:] = [{'resources': [{'name': 'meta', 'types': ['movie']}]}]
        d_meta = {'meta': {'id': 'tt9', 'name': 'Estreno'}}
        restore = patch_fetch(lambda *a, **k: d_meta)
        try:
            d = M.handle_meta('series', 'tt9')
            self.assertEqual(d['meta']['name'], 'Estreno')
        finally:
            restore()


if __name__ == '__main__':
    unittest.main()
