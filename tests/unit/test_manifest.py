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





class TestNoEmoji(unittest.TestCase):
    def test_todo_emoji_cae_al_original(self):
        """s2 vacío -> s.strip() (Or@60: and -> '' deja el nombre en blanco)."""
        self.assertEqual(MF._noemoji(' 🎬 '), '🎬')
        self.assertEqual(MF._noemoji('Top 🎬 Movies'), 'Top Movies')


class TestEffRankExacto(unittest.TestCase):
    def test_aritmetica_source_order(self):
        """min(_RANK) - len(order) + idx (Sub@68); default 99 (99+1@69)."""
        _, undo = cfg_swap(source_order=['Kitsu', 'Cinemeta'])
        try:
            base = min(MF._RANK.values())
            self.assertEqual(MF._eff_rank('Kitsu'), base - 2)
            self.assertEqual(MF._eff_rank('Cinemeta'), base - 1)
            self.assertEqual(MF._eff_rank('NoListado'), 99)
        finally:
            undo()


class TestMfCatReq(unittest.TestCase):
    def test_search_requerido_es_opcion_muerta(self):
        """req con 'search' -> None (NotIn@100: 'in' lo dejaría pasar)."""
        c = {'id': 'x', 'extra': [{'name': 'search', 'isRequired': True}]}
        self.assertIsNone(MF._mf_cat_req(c))
        c2 = {'id': 'x', 'extra': [{'name': 'genre', 'isRequired': True}]}
        self.assertEqual(MF._mf_cat_req(c2), ['genre'])

    def test_extra_required_feed_ok(self):
        c = {'id': 'x', 'extraRequired': ['lastVideosIds']}
        self.assertEqual(MF._mf_cat_req(c), ['lastVideosIds'])


class TestMfOneCatKeep(unittest.TestCase):
    def test_keep_regex(self):
        """keep.search sobre id (not-elim@108 invertiría: keep descarta)."""
        from conflux.core.state import Source
        src = Source('X', 'u')
        keep = __import__('re').compile('top')
        seen = set()
        self.assertIsNotNone(
            MF._mf_one_cat(src, {'id': 'top1', 'type': 'movie',
                                 'name': 'Top'}, keep, seen))
        self.assertIsNone(
            MF._mf_one_cat(src, {'id': 'other', 'type': 'movie',
                                 'name': 'Other'}, keep, seen))


class TestMfNcDeadOpt(unittest.TestCase):
    def test_dead_opt_filtra_opciones(self):
        """_DEAD_OPT poda opciones muertas (NotIn@128), solo entradas genre
        (Eq/And@129), y solo si hay _DEAD_OPT (And@126: or vaciaría todo)."""
        from conflux.core.state import Source
        old = set(st._DEAD_OPT)
        src = Source('X', 'u')
        c = {'id': 'c1', 'type': 'movie', 'name': 'C',
             'extra': [{'name': 'genre', 'options': ['a', 'b']},
                       {'name': 'otro', 'options': ['z']}]}
        try:
            st._DEAD_OPT.clear()
            nc = MF._mf_nc(src, c, 'sx_movie_c1')
            g = next(e for e in nc['extra'] if e['name'] == 'genre')
            self.assertEqual(g['options'], ['a', 'b'])          # vacío -> intacto
            st._DEAD_OPT.update({('sx_movie_c1', 'a'), ('sx_movie_c1', 'z')})
            nc = MF._mf_nc(src, c, 'sx_movie_c1')
            g = next(e for e in nc['extra'] if e['name'] == 'genre')
            self.assertEqual(g['options'], ['b'])               # 'a' muerta fuera
            o = next(e for e in nc['extra'] if e['name'] == 'otro')
            self.assertEqual(o['options'], ['z'])   # no-genre NUNCA se filtra
        finally:
            st._DEAD_OPT.clear(); st._DEAD_OPT.update(old)


class TestMfCollectCats(unittest.TestCase):
    def test_manifest_extra_y_salto(self):
        """manifests>SOURCES se ignora (GtE@143); falsy/no_catalog/disabled se
        saltan (Or@145: and no saltaría)."""
        from conflux.core.state import Source
        man = {'catalogs': [{'id': 'c1', 'type': 'movie', 'name': 'C'}]}
        old_no = set(MF._NO_CATALOG)
        try:
            with patch.object(MF, 'SOURCES',
                              [Source('S0', 'u'), Source('S1', 'v'),
                               Source('S2', 'w')]), \
                    patch.object(MF, 'manifests',
                                 [man, None, man, man]), \
                    patch.object(MF, '_eff_disabled', return_value={'S2'}):
                MF._NO_CATALOG.add('S1')
                cats = MF._mf_collect_cats()
        finally:
            MF._NO_CATALOG.clear(); MF._NO_CATALOG.update(old_no)
        # S0 aporta; S1=None salta; S2 disabled salta; idx3 fuera de SOURCES
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0][1]['id'], 'ss0_movie_c1')


class TestYearOpts(unittest.TestCase):
    def test_year_opts_todos_4digit(self):
        """bool(yopts) and all(\\d{4}) (And@164: or aceptaría mezcla)."""
        self.assertTrue(MF._year_opts(['2020', '2024']))
        self.assertFalse(MF._year_opts(['2020', 'accion']))
        self.assertFalse(MF._year_opts([]))

    def test_mf_year_opts_solo_genre_requerido(self):
        """Único extra requerido = genre año -> yopts (Or@155, Eq@156/157,
        And@157)."""
        nc = {'extra': [{'name': 'genre', 'isRequired': True,
                         'options': ['2020', '2024']}]}
        self.assertEqual(MF._mf_year_opts(nc), ['2020', '2024'])
        nc2 = {'extra': [{'name': 'genre', 'isRequired': False,
                          'options': ['2020']}]}                    # no requerido
        self.assertIsNone(MF._mf_year_opts(nc2))
        nc3 = {'extra': [{'name': 'genre', 'isRequired': True,
                          'options': ['accion']}]}                  # no-años
        self.assertIsNone(MF._mf_year_opts(nc3))
        nc4 = {'extra': [{'name': 'genre', 'isRequired': True,
                          'options': ['2020']},
                         {'name': 'search', 'isRequired': True}]}   # 2 requeridos
        self.assertIsNone(MF._mf_year_opts(nc4))


class TestMfGroup(unittest.TestCase):
    def _cats(self, subs1=('Accion',), extra2=None):
        e1 = {'name': 'genre', 'isRequired': False, 'options': list(subs1)}
        e2 = extra2 if extra2 is not None else {
            'name': 'genre', 'isRequired': False, 'options': ['Otro']}
        return [(0, {'id': 'sx_movie_a', 'type': 'movie',
                     'name': 'X · A', 'extra': [e1]}),
                (0, {'id': 'sx_movie_b', 'type': 'movie',
                     'name': 'X · B', 'extra': [e2]})]

    def setUp(self):
        self._g = dict(MF._GRP); self._y = dict(MF._YR_OPTS)

    def tearDown(self):
        MF._GRP.clear(); MF._GRP.update(self._g)
        MF._YR_OPTS.clear(); MF._YR_OPTS.update(self._y)

    def test_grupo_pliega_y_expone_subs(self):
        """2+ cats mismo (src,tipo) -> gid con selector 'Label · sub'
        (Eq@203, not-elim@204, In@207) y miembros plegados fuera (NotIn@239)."""
        out = MF._mf_group(self._cats())
        gids = [nc['id'] for _, nc in out]
        self.assertIn('sx_movie_grp', gids)
        self.assertNotIn('sx_movie_a', gids)     # plegado
        self.assertNotIn('sx_movie_b', gids)
        opts = [e['options'] for e in out[0][1]['extra'] if e['name'] == 'genre'][0]
        self.assertIn('A · Accion', opts)         # sub-género alcanzable
        self.assertIn('B', opts)
        ge = next(e for e in out[0][1]['extra'] if e['name'] == 'genre')
        self.assertIs(ge['isRequired'], False)    # False@181

    def test_yr_opts_descendente(self):
        """_YR_OPTS sorted reverse=True (True@234)."""
        cats = [(0, {'id': 'sx_movie_a', 'type': 'movie', 'name': 'X · A',
                     'extra': [{'name': 'genre', 'isRequired': True,
                                'options': ['2020']}]}),
                (0, {'id': 'sx_movie_b', 'type': 'movie', 'name': 'X · B',
                     'extra': [{'name': 'genre', 'isRequired': True,
                                'options': ['2024', '2019']}]})]
        MF._REQ.update({'sx_movie_a', 'sx_movie_b'})
        try:
            out = MF._mf_group(cats)
        finally:
            MF._REQ.difference_update({'sx_movie_a', 'sx_movie_b'})
        self.assertEqual(MF._YR_OPTS['sx_movie_grp'],
                         ['2024', '2020', '2019'])
        byid = {nc['id']: nc for _, nc in out}
        ye = next(e for e in byid['sx_movie_grp']['extra']
                  if e['name'] == 'year')
        self.assertIs(ye['isRequired'], False)    # False@183

    def test_subs_cap_20(self):
        """subs[:20] por miembro (20+1@205)."""
        many = [f'g{i}' for i in range(30)]
        out = MF._mf_group(self._cats(subs1=tuple(many)))
        byid = {nc['id']: nc for _, nc in out}
        opts = next(e['options'] for e in byid['sx_movie_grp']['extra']
                    if e['name'] == 'genre')
        self.assertIn('A · g19', opts)
        self.assertNotIn('A · g20', opts)

    def test_extra_search_skip_si_upstream(self):
        """search/skip entran al grupo si algún miembro los declara (In@184)."""
        cats = self._cats(extra2={'name': 'search', 'isRequired': False})
        out = MF._mf_group(cats)
        byid = {nc['id']: nc for _, nc in out}
        extra = byid['sx_movie_grp']['extra']
        names = {e['name'] for e in extra}
        self.assertIn('search', names)
        self.assertNotIn('skip', names)           # no declarado upstream
        se = next(e for e in extra if e['name'] == 'search')
        self.assertIs(se['isRequired'], False)    # False@184


class TestSynthFlags(unittest.TestCase):
    def setUp(self):
        self._s = dict(MF._SYNTH)

    def tearDown(self):
        MF._SYNTH.clear(); MF._SYNTH.update(self._s)

    def test_isrequired_flags(self):
        """estrenos genre False (False@261); calendarVideosIds/lastVideosIds/
        search True (True@266/275/285); rank+1 de listas (Add@295)."""
        cats = []
        MF._mf_synth_cats(cats)
        d = {nc['id']: nc for _, nc in cats}
        est = next(e for e in d['scinemeta_movie_estrenos']['extra']
                   if e['name'] == 'genre')
        self.assertIs(est['isRequired'], False)
        cal = d['scinemeta_series_estrenoscal']['extra'][0]
        self.assertEqual(cal['name'], 'calendarVideosIds')
        self.assertIs(cal['isRequired'], True)
        lv = d['scinemeta_series_lastvids']['extra'][0]
        self.assertIs(lv['isRequired'], True)
        for t in ('movie', 'series'):
            s = d[f'suniform_{t}_search']['extra'][0]
            self.assertIs(s['isRequired'], True)

    def test_m3u_path_inexistente_sin_cat(self):
        """m3u_path set pero el fichero no existe -> sin catálogo
        (And@312: or entraría igualmente)."""
        _, undo = cfg_swap(m3u_path='/no/existe/este.m3u')
        try:
            cats = []
            MF._mf_m3u_cat(cats)
        finally:
            undo()
        self.assertEqual(cats, [])

    def test_m3u_date_opcional(self):
        """extra date isRequired False (False@317)."""
        with tempfile.NamedTemporaryFile(suffix='.m3u', delete=False) as f:
            f.write(b'#EXTM3U\n')
            path = f.name
        _, undo = cfg_swap(m3u_path=path)
        try:
            cats = []
            MF._mf_m3u_cat(cats)
        finally:
            undo(); os.unlink(path)
        byid = {nc['id']: nc for _, nc in cats}
        self.assertIs(byid['sm3u_tv_channels']['extra'][0]['isRequired'], False)


class TestBuildManifestFlags(unittest.TestCase):
    def test_catalogs_disabled_filtra(self):
        """catalogs_disabled excluye cids (Or@345: and -> [] no filtra)."""
        old = list(px.manifests)
        _, undo = cfg_swap(catalogs_disabled=['scinemeta_movie_estrenos'])
        try:
            px.manifests[:] = []
            man = px.build_manifest()
        finally:
            px.manifests[:] = old; undo()
        ids = {c['id'] for c in man['catalogs']}
        self.assertNotIn('scinemeta_movie_estrenos', ids)

    def test_behavior_hints_flags(self):
        """newEpisodeNotifications + configurable True (True@357/358)."""
        man = px.build_manifest()
        bh = man['behaviorHints']
        self.assertIs(bh['newEpisodeNotifications'], True)
        self.assertIs(bh['configurable'], True)

    def test_epg_provider_solo_con_m3u(self):
        """epgProvider solo si existe el catálogo sm3u (Eq@361)."""
        man = px.build_manifest()
        self.assertNotIn('epgProvider', man['behaviorHints'])
        with tempfile.NamedTemporaryFile(suffix='.m3u', delete=False) as f:
            f.write(b'#EXTM3U\n')
            path = f.name
        _, undo = cfg_swap(m3u_path=path)
        try:
            man2 = px.build_manifest()
        finally:
            undo(); os.unlink(path)
        self.assertIs(man2['behaviorHints']['epgProvider'], True)


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
        self.assertEqual(cats[0][0], MF._eff_rank('Cinemeta') + 1)  # Add@295


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
