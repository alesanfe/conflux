# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.manifest — contrato R3."""
import os
import tempfile
import unittest
from unittest.mock import patch

import conflux.domain.manifest as MF
import conflux.core.state as st
from tests import cfg_swap, px


class TestReglas(unittest.TestCase):
    """Reglas generalizadas extraídas de los bugs reales (ver AGENTS.md)."""

    def _manifest_sintetico(self):
        old = list(px.manifests)
        px.manifests[:] = [{'resources': ['catalog'], 'catalogs': [
            {'type': 'movie', 'id': 'a', 'name': 'A'},
            {'type': 'series', 'id': 'a', 'name': 'A'},
            {'type': 'series', 'id': 'animes_top', 'name': 'Anime Top'},
            {'type': 'series', 'id': 'animes_top', 'name': 'Anime Top'},
        ]}] * len(px.SOURCES[:1])
        try:
            return px.build_manifest()
        finally:
            px.manifests[:] = old

    def test_idprefixes_minimos(self):
        prefs = set(px.build_manifest()['idPrefixes'])
        for p in ('tt', 'kitsu', 'tmdb', 'mx', '_onepace'):
            self.assertIn(p, prefs, f'idPrefixes debe declarar {p}')

    def test_catalog_ids_unicos_y_parseables(self):
        man = self._manifest_sintetico()
        ids = [c['id'] for c in man['catalogs']]
        self.assertEqual(len(ids), len(set(ids)), 'cids duplicados')
        for cid in ids:
            _slug, rest = cid.split('_', 1)
            utype, _, real = rest.partition('_')
            self.assertIn(utype, {'movie', 'series', 'anime', 'channel', 'tv'},
                          f'{cid}: tipo upstream ilegible')
            self.assertTrue(real, f'{cid}: id upstream vacío')

    def test_anime_remap_conserva_utype(self):
        man = self._manifest_sintetico()
        anime = [c for c in man['catalogs'] if 'animes_top' in c['id']]
        self.assertTrue(anime and all(c['type'] == 'anime' for c in anime),
                        'catálogo anime debe servirse como tipo anime')
        self.assertTrue(all(px._UTYPE[c['id']] == 'series' for c in anime),
                        '_UTYPE debe conservar el tipo real para la llamada upstream')

    def test_nombres_catalogo_sin_duplicados(self):
        man = self._manifest_sintetico()
        pares = [(c['name'], c['type']) for c in man['catalogs']]
        self.assertEqual(len(pares), len(set(pares)),
                         [p for p in pares if pares.count(p) > 1])

    def test_catalog_dupes_upstream_fuera(self):
        man = self._manifest_sintetico()
        self.assertEqual(
            sum(1 for c in man['catalogs'] if 'animes_top' in c['id']), 1)


if __name__ == '__main__':
    unittest.main()





class TestEffRank(unittest.TestCase):
    def test_source_order_manda(self):
        import conflux.core.state as st
        old = dict(st.CFG)
        st.CFG['source_order'] = ['Kitsu', 'Cinemeta']
        try:
            r_kitsu = MF._eff_rank('Kitsu')
            r_cine = MF._eff_rank('Cinemeta')
            r_otro = MF._eff_rank('Otro')
        finally:
            st.CFG.clear(); st.CFG.update(old)
        self.assertLess(r_kitsu, r_cine)
        self.assertLess(r_cine, r_otro)


class TestCatName(unittest.TestCase):
    def test_prefijo_redundante(self):
        c = {'id': 'x', 'name': 'AllIPTV · Deportes'}
        self.assertEqual(MF._cat_name('AllIPTV', c), 'AllIPTV · Deportes')

    def test_tmdb_punto(self):
        c = {'id': 'x', 'name': 'tmdb.top'}
        self.assertEqual(MF._cat_name('TMDB', c), 'TMDB · top')

    def test_nombre_vacio_fallback_id(self):
        c = {'id': 'best-movies', 'name': 'TMDB'}
        self.assertEqual(MF._cat_name('TMDB', c), 'TMDB · Movies')

    def test_nombre_igual_a_fuente(self):
        c = {'id': 'kitsu', 'name': 'Kitsu'}
        self.assertEqual(MF._cat_name('Kitsu', c), 'Kitsu')


class TestMfListCats(unittest.TestCase):
    def setUp(self):
        self._s = dict(MF._SYNTH)

    def tearDown(self):
        MF._SYNTH.clear(); MF._SYNTH.update(self._s)

    def test_lista_genera_catalogos(self):
        _, undo = cfg_swap(catalog_lists='mdblist:1')
        try:
            with patch.object(MF, '_list_fetch',
                              return_value=('Top', [('movie', 'tt1'), ('series', 'tt2')])):
                cats = []
                MF._mf_list_cats(cats)
        finally:
            undo()
        self.assertEqual(len(cats), 2)
        ids = {c['id'] for _, c in cats}
        self.assertIn('slist0_movie', ids)
        self.assertIn('slist0_series', ids)
        self.assertIn('slist0_movie', MF._SYNTH)


class TestMfRecomCats(unittest.TestCase):
    def setUp(self):
        self._s = dict(MF._SYNTH)

    def tearDown(self):
        MF._SYNTH.clear(); MF._SYNTH.update(self._s)

    def test_con_token_dos_catalogos(self):
        _, undo = cfg_swap(trakt_access_token='AT')
        try:
            cats = []
            MF._mf_recom_cats(cats)
        finally:
            undo()
        self.assertEqual(len(cats), 2)
        self.assertIn('suniform_movie_recom', MF._SYNTH)
        self.assertIn('suniform_series_recom', MF._SYNTH)

    def test_sin_token_vacio(self):
        _, undo = cfg_swap(trakt_access_token='')
        try:
            cats = []
            MF._mf_recom_cats(cats)
        finally:
            undo()
        self.assertEqual(cats, [])


class TestMfM3uCat(unittest.TestCase):
    def setUp(self):
        self._s = dict(MF._SYNTH)

    def tearDown(self):
        MF._SYNTH.clear(); MF._SYNTH.update(self._s)

    def test_con_m3u_crea_catalogo(self):
        with tempfile.NamedTemporaryFile(suffix='.m3u', delete=False) as f:
            f.write(b'#EXTM3U\n')
            path = f.name
        _, undo = cfg_swap(m3u_path=path)
        try:
            cats = []
            MF._mf_m3u_cat(cats)
        finally:
            undo(); os.unlink(path)
        self.assertEqual(len(cats), 1)
        self.assertIn('sm3u_tv_channels', MF._SYNTH)

    def test_sin_m3u_vacio(self):
        _, undo = cfg_swap(m3u_path='')
        try:
            cats = []
            MF._mf_m3u_cat(cats)
        finally:
            undo()
        self.assertEqual(cats, [])


class TestMfDedupNames(unittest.TestCase):
    def test_duplicados_sufijados(self):
        cats = [{'id': 'a1', 'type': 'movie', 'name': 'Top'},
                {'id': 'a2', 'type': 'movie', 'name': 'Top'},
                {'id': 'a3', 'type': 'movie', 'name': 'Top'},
                {'id': 'b', 'type': 'movie', 'name': 'Otro'}]
        MF._UTYPE['a1'] = 'movie'; MF._UTYPE['a2'] = 'series'; MF._UTYPE['a3'] = 'series'
        MF._mf_dedup_names(cats)
        self.assertEqual(cats[1]['name'], 'Top (Series)')
        self.assertEqual(cats[2]['name'], 'Top (Series 2)')
        self.assertEqual(cats[3]['name'], 'Otro')


class TestBrowseablesSolo(unittest.TestCase):
    """Feed-app (lastVideosIds/calendarVideosIds) y filtros upstream no son
    opciones navegables — los feed los sirven los sintéticos."""

    def _cats(self):
        old = list(px.manifests)
        old_dead = set(st._DEAD_OPT)
        st._DEAD_OPT.clear()                    # marcas de otros tests no ocultan aquí
        px.manifests[:] = [{'resources': ['catalog'], 'catalogs': [
            {'type': 'series', 'id': 'top', 'name': 'Top'},
            {'type': 'series', 'id': 'pop', 'name': 'Pop'},
            {'type': 'series', 'id': 'last-videos', 'name': 'Last videos',
             'extra': [{'name': 'lastVideosIds', 'isRequired': True}]},
            {'type': 'series', 'id': 'calendar-videos', 'name': 'Calendar videos',
             'extra': [{'name': 'calendarVideosIds', 'isRequired': True}]},
            {'type': 'series', 'id': 'series_filter', 'name': 'Series Filter',
             'extra': [{'name': 'genre', 'isRequired': False}]},
        ]}] * len(px.SOURCES[:1])
        try:
            return px.build_manifest()
        finally:
            px.manifests[:] = old
            st._DEAD_OPT.clear()
            st._DEAD_OPT.update(old_dead)

    def test_feed_y_filter_fuera_de_opciones(self):
        man = self._cats()
        cine = next(c for c in man['catalogs'] if c['id'].endswith('_grp')
                    and c['type'] == 'series')
        opts = next(e['options'] for e in cine['extra'] if e['name'] == 'genre')
        for bad in ('Last videos', 'Calendar videos', 'Series Filter'):
            self.assertNotIn(bad, opts)
        self.assertEqual(opts, ['Top', 'Pop'])

    def test_lastvids_sintetico_cubre_continue_watching(self):
        man = self._cats()
        lv = next((c for c in man['catalogs'] if 'lastvids' in c['id']), None)
        self.assertIsNotNone(lv)
        self.assertEqual([e['name'] for e in lv['extra']], ['lastVideosIds'])
