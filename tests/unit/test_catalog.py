# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.catalog — routing + dispatch sintéticos."""
import collections
import unittest
from unittest.mock import patch

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
        C._search_merge({'metas': [{'id': 'a'}, {'id': 'b'}]}, 'movie', seen, out)
        C._search_merge({'metas': [{'id': 'a'}, {'id': 'c'}]}, 'movie', seen, out)
        self.assertEqual([m['id'] for m in out], ['a', 'b', 'c'])

    def test_merge_vacio(self):
        seen, out = set(), []
        C._search_merge(None, 'movie', seen, out)
        C._search_merge({}, 'movie', seen, out)
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


class TestSearchKeyCanonico(unittest.TestCase):
    """Dedup por id canónico: kitsu:X y su tt resuelto = mismo meta."""

    def test_kitsu_dedup_con_tt(self):
        seen, out = set(), []
        with patch.object(C, '_resolve_sid', return_value='tt0409591') as rs:
            C._search_merge({'metas': [{'id': 'kitsu:1'}]}, 'series', seen, out)
            C._search_merge({'metas': [{'id': 'tt0409591'}]}, 'series', seen, out)
        rs.assert_called_once_with('series', 'kitsu:1')
        self.assertEqual([m['id'] for m in out], ['kitsu:1'],
                         'kitsu:1 resuelve a tt0409591 -> el tt posterior es dupe')

    def test_tt_sin_resolucion(self):
        seen, out = set(), []
        with patch.object(C, '_resolve_sid') as rs:
            C._search_merge({'metas': [{'id': 'tt1'}]}, 'movie', seen, out)
        rs.assert_not_called()          # tt: resolución gratis, sin fetch
        self.assertEqual(len(out), 1)


class TestCatFixExtra(unittest.TestCase):
    """_cat_fix_extra: 'genre' que upstream no declara ('Top' inyectado por
    Stremio en el selector de canales) -> se quita y sirve el catálogo base."""

    def setUp(self):
        import conflux.core.state as st
        self._man = list(st.manifests)
        st.manifests[:] = [{'catalogs': [
            {'id': 'iptv-all', 'type': 'tv',
             'extra': [{'name': 'genre', 'options': ['auto', 'sports']}]}]}]

    def tearDown(self):
        import conflux.core.state as st
        st.manifests[:] = self._man

    def test_genre_desconocido_fuera(self):
        import conflux.core.state as st
        self.assertEqual(
            C._cat_fix_extra(st.SOURCES[0].base, 'tv', 'iptv-all', 'genre=Top'), '')

    def test_genre_valido_pasa(self):
        import conflux.core.state as st
        self.assertEqual(C._cat_fix_extra(st.SOURCES[0].base, 'tv', 'iptv-all',
                                          'genre=sports'), 'genre=sports')

    def test_sin_genre_intacto(self):
        import conflux.core.state as st
        self.assertEqual(C._cat_fix_extra(st.SOURCES[0].base, 'tv', 'iptv-all',
                                          'skip=50'), 'skip=50')

    def test_case_insensitive(self):
        import conflux.core.state as st
        self.assertEqual(C._cat_fix_extra(st.SOURCES[0].base, 'tv', 'iptv-all',
                                          'genre=SPORTS'), 'genre=SPORTS')


class TestDeadOptGrp(unittest.TestCase):
    """Opciones muertas en grupos: label muerta -> base; base muerta -> oculto."""

    def setUp(self):
        import conflux.core.state as st
        self._dead = set(st._DEAD_OPT)
        self._grp = dict(C._GRP)
        C._GRP['sg_tst_grp'] = collections.OrderedDict(
            [('Vivo', ('movie', 'real1', False, None)),
             ('Muerto', ('movie', 'real2', False, 'x'))])

    def tearDown(self):
        import conflux.core.state as st
        st._DEAD_OPT.clear()
        st._DEAD_OPT.update(self._dead)
        C._GRP.clear()
        C._GRP.update(self._grp)

    def test_label_muerta_cae_a_base(self):
        import conflux.core.state as st
        st._DEAD_OPT.add(('sg_tst_grp', 'muerto'))
        ut, real, extra = C._cat_grp_extra('sg_tst_grp', 'genre=Muerto')
        self.assertEqual(real, 'real1')          # fallback: primer miembro
        self.assertNotIn('genre=', extra)

    def test_base_salta_a_miembro_vivo(self):
        import conflux.core.state as st
        st._DEAD_OPT.add(('sg_tst_grp', 'vivo'))
        ut, real, extra = C._cat_grp_extra('sg_tst_grp', None)
        self.assertEqual(real, 'real2')          # 1º muerto -> sirve el vivo

    def test_label_viva_intacta(self):
        ut, real, extra = C._cat_grp_extra('sg_tst_grp', 'genre=Muerto')
        self.assertEqual(real, 'real2')
        self.assertIn('genre=x', extra)

    def test_base_muerta_oculta_catalogo(self):
        import conflux.core.state as st
        st._DEAD_OPT.add(('sfoo_movie_bar', ''))
        src = type('S', (), {'name': 'Foo', 'base': 'http://x'})()
        r = mf._mf_one_cat(src, {'id': 'bar', 'type': 'movie', 'name': 'Bar'},
                           None, set())
        self.assertIsNone(r)
