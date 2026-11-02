"""Unit tests conflux.app.meta — /meta routing."""
import unittest
from unittest.mock import patch

import conflux.app.meta as M
import conflux.core.state as st


class TestMetaTry(unittest.TestCase):
    def test_series_fallback_movie(self):
        self.assertEqual(M._meta_try('series'), {'series', 'movie'})

    def test_movie_sin_fallback(self):
        self.assertEqual(M._meta_try('movie'), {'movie'})


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


if __name__ == '__main__':
    unittest.main()
