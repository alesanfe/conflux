# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.catalog — routing + dispatch sintéticos."""
import unittest

import conflux.domain.catalog as C
import conflux.domain.manifest as mf


class TestCatRoute(unittest.TestCase):
    def test_cid_valido(self):
        _base, utype, real = C._cat_route('scinemeta_movie_top', 'movie')
        self.assertIn('movie', (utype, 'movie'))
        self.assertEqual(real, 'top')

    def test_cid_invalido_levanta(self):
        with self.assertRaises((ValueError, StopIteration, IndexError)):
            C._cat_route('noexiste', 'movie')


class TestSearchMerge(unittest.TestCase):
    def test_merge_dedup(self):
        seen, out = set(), []
        C._search_merge({'metas': [{'id': 'a'}, {'id': 'b'}]}, seen, out)
        C._search_merge({'metas': [{'id': 'a'}, {'id': 'c'}]}, seen, out)
        self.assertEqual([m['id'] for m in out], ['a', 'b', 'c'])

    def test_merge_vacio(self):
        seen, out = set(), []
        C._search_merge(None, seen, out)
        C._search_merge({}, seen, out)
        self.assertEqual(out, [])


class TestSynthDispatch(unittest.TestCase):
    def test_busqueda_via_synth(self):
        from tests import patch_fetch
        mf._SYNTH['suniform_movie_search'] = ('movie', '_busqueda', None)
        restore = patch_fetch(lambda *a, **k: None)
        try:
            r = C._cat_synth('suniform_movie_search', 'movie', 'search=x', 'k')
            self.assertEqual(r[0], {'metas': []})
            self.assertEqual(r[2], '_busqueda')
        finally:
            restore()

    def test_desconocido_none(self):
        mf._SYNTH['sx_t'] = ('movie', '_nohandler', None)
        resp, _u, _r, _e = C._cat_synth('sx_t', 'movie', None, 'k')
        self.assertIsNone(resp)


class TestXlateIds(unittest.TestCase):
    def test_extra_sin_ids_intacto(self):
        self.assertEqual(C._cat_xlate_ids('movie', 'genre=2024'), 'genre=2024')


if __name__ == '__main__':
    unittest.main()
