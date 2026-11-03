# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.app.meta — /meta routing."""
import unittest
from unittest.mock import patch

import conflux.app.meta as M
import conflux.core.state as st


class TestMetaTry(unittest.TestCase):
    def test_series_fallback_movie(self):
        self.assertEqual(M._meta_try('series'), ('series', 'movie'))  # orden: propio primero

    def test_movie_sin_fallback(self):
        self.assertEqual(M._meta_try('movie'), ('movie',))


class TestMetaOne(unittest.TestCase):
    def test_manifest_sin_meta_skip(self):
        with patch.dict(M.__dict__, {'manifests': [{'resources': [{'name': 'stream'}]}]}):
            self.assertIsNone(M._meta_one(0, 'movie', 'tt1'))

    def test_tipo_no_declarado_skip(self):
        m = {'resources': [{'name': 'meta', 'types': ['movie']}]}
        with patch.dict(M.__dict__, {'manifests': [m]}):
            self.assertIsNone(M._meta_one(0, 'series', 'tt1'))


class TestHandleMeta(unittest.TestCase):
    def test_cache_hit(self):
        key = ('movie', 'tt1')
        st._meta_c[key] = (__import__('time').time(), {'meta': {'id': 'tt1'}})
        self.assertEqual(M.handle_meta('movie', 'tt1'), {'meta': {'id': 'tt1'}})
        st._meta_c.clear()

    def test_prefetch_on_llama_collect(self):
        with patch.dict(M.__dict__, {'manifests': []}), \
                patch.object(M, '_ensure_collect') as ec:
            M.handle_meta('movie', 'tt999')
            ec.assert_called_once_with(
                ('movie', 'tt999', tuple(sorted(M._eff_disabled()))))

    def test_prefetch_off_no_collect(self):
        """stream_prefetch=False: abrir la ficha NO lanza consultas a fuentes."""
        from tests import cfg_swap
        _, undo = cfg_swap(stream_prefetch=False)
        try:
            with patch.dict(M.__dict__, {'manifests': []}), \
                    patch.object(M, '_ensure_collect') as ec:
                M.handle_meta('movie', 'tt999')
                ec.assert_not_called()
        finally:
            undo()


if __name__ == '__main__':
    unittest.main()
