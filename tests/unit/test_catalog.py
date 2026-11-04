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


# ---------------------------------------------------------------------------
# Contrato de routing: cada guarda de _cat_* distingue su caso — estas fijan
# los bordes que la mutación AST demostró débiles (24% kill rate).
# ---------------------------------------------------------------------------

def _src(name='Foo', base='http://b'):
    return type('S', (), {'name': name, 'base': base})()


class TestCatSearchable(unittest.TestCase):
    """_cat_searchable: tipo (real o via _UTYPE) + 'search' en extra o extraSupported."""

    def test_search_en_extra(self):
        c = {'id': 'x', 'type': 'movie', 'extra': [{'name': 'search'}]}
        self.assertTrue(C._cat_searchable(_src(), c, 'movie'))

    def test_search_solo_en_supported(self):
        c = {'id': 'x', 'type': 'movie', 'extraSupported': ['search']}
        self.assertTrue(C._cat_searchable(_src(), c, 'movie'))

    def test_sin_search_false(self):
        c = {'id': 'x', 'type': 'movie', 'extra': [{'name': 'genre'}]}
        self.assertFalse(C._cat_searchable(_src(), c, 'movie'))

    def test_tipo_erroneo_false(self):
        c = {'id': 'x', 'type': 'movie', 'extra': [{'name': 'search'}]}
        self.assertFalse(C._cat_searchable(_src(), c, 'series'))

    def test_tipo_via_utype(self):
        """El tipo aceptado es ct (via _UTYPE) O c['type'] — ambos pasan."""
        src = _src('Foo')
        key = 'sfoo_movie_x'
        with patch.object(C, '_UTYPE', {key: 'anime'}):
            c = {'id': 'x', 'type': 'movie', 'extra': [{'name': 'search'}]}
            self.assertTrue(C._cat_searchable(src, c, 'anime'))
            self.assertTrue(C._cat_searchable(src, c, 'movie'))
            self.assertFalse(C._cat_searchable(src, c, 'series'))


class TestSearchTargets(unittest.TestCase):
    """_search_targets: skips manifest vacío y fuente deshabilitada."""

    def setUp(self):
        import conflux.core.state as st
        self._man = list(st.manifests)

    def tearDown(self):
        import conflux.core.state as st
        st.manifests[:] = self._man

    def test_manifest_vacio_saltado(self):
        import conflux.core.state as st
        st.manifests[:] = [None] * len(st.SOURCES)
        self.assertEqual(C._search_targets('movie'), [])

    def test_recoge_base_tipo_id(self):
        import conflux.core.state as st
        st.manifests[:] = [{'catalogs': [
            {'id': 'top', 'type': 'movie', 'extra': [{'name': 'search'}]}]}] + \
            [None] * (len(st.SOURCES) - 1)
        tg = C._search_targets('movie')
        self.assertEqual(tg, [(st.SOURCES[0].base, 'movie', 'top')])

    def test_deshabilitada_saltada(self):
        import conflux.core.state as st
        st.manifests[:] = [{'catalogs': [
            {'id': 'top', 'type': 'movie', 'extra': [{'name': 'search'}]}]}] + \
            [None] * (len(st.SOURCES) - 1)
        with patch.object(C, '_eff_disabled', return_value={st.SOURCES[0].name}):
            self.assertEqual(C._search_targets('movie'), [])


class TestBusqueda(unittest.TestCase):
    """_busqueda: sin q -> []; fan-out con URL correcta; excepciones toleradas;
    early-done cuando quedan <=3 stragglers tras 2s."""

    def test_sin_query_vacio(self):
        self.assertEqual(C._busqueda('movie', 'genre=x'), {'metas': []})
        self.assertEqual(C._busqueda('movie', None), {'metas': []})

    def test_fanout_url_y_metas(self):
        with patch.object(C, '_search_targets',
                          return_value=[('http://b', 'movie', 'top')]), \
             patch.object(C, 'fetch',
                          return_value={'metas': [{'id': 'tt1'}]}) as f:
            out = C._busqueda('movie', 'search=dune')
        f.assert_called_once_with('http://b/catalog/movie/top/search=dune.json', 8)
        self.assertEqual(out['metas'][0]['id'], 'tt1')

    def test_timeout_devuelve_parcial(self):
        with patch.object(C, '_search_targets',
                          return_value=[('http://b', 'movie', 'top')]), \
             patch.object(C, 'fetch', return_value=None), \
             patch.object(C, 'as_completed', side_effect=TimeoutError):
            self.assertEqual(C._busqueda('movie', 'search=x'), {'metas': []})

    def test_future_roto_continua(self):
        with patch.object(C, '_search_targets',
                          return_value=[('http://b', 'movie', 'top')]), \
             patch.object(C, 'fetch', side_effect=RuntimeError('boom')):
            out = C._busqueda('movie', 'search=x')
        self.assertEqual(out, {'metas': []})     # el error no aborta el merge

    def test_early_done_stragglers(self):
        """el>2s y quedan <=3 -> break: no retiene la respuesta por los últimos."""
        tgts = [('http://b', 'movie', f'c{i}') for i in range(5)]
        n = [0]
        def ff(url, t):
            n[0] += 1
            return {'metas': [{'id': f'tt{n[0]:07d}'}]}   # ids únicos: sin dedup
        times = iter([0.0] + [3.0] * 10)          # t0=0; el=3 en el bucle
        faketime = type('T', (), {'time': staticmethod(lambda: next(times))})
        with patch.object(C, '_search_targets', return_value=tgts), \
             patch.object(C, 'fetch', side_effect=ff), \
             patch.object(C, 'time', faketime):
            out = C._busqueda('movie', 'search=x')
        # 5 fuentes: done=1 -> quedan 4 (>3); done=2 -> quedan 3 -> break
        self.assertEqual(len(out['metas']), 2)


class TestCatRouteBordes(unittest.TestCase):
    """_cat_route: slug legacy sN_, slug desconocido+sintético, utype fallback."""

    def test_legacy_digit(self):
        """s6_xxx -> _LEGACY[6] por nombre (no por posición)."""
        from conflux.domain.manifest import _LEGACY
        if len(_LEGACY) > 6:
            base, utype, real = C._cat_route('s6_movie_x', 'movie')
            self.assertIsNotNone(base)

    def test_slug_desconocido_sintetico_base_none(self):
        mf._SYNTH['sx_movie_y'] = ('movie', '_nohandler', None)
        try:
            base, utype, real = C._cat_route('sx_movie_y', 'movie')
            self.assertIsNone(base)                      # sintético sin fuente
            self.assertEqual(real, 'y')
        finally:
            del mf._SYNTH['sx_movie_y']

    def test_utype_invalido_fallback_utype_map(self):
        """rest sin tipo conocido -> utype via _UTYPE[cid] o stype."""
        base, utype, real = C._cat_route('scinemeta_zzz', 'series')
        self.assertEqual(real, 'zzz')
        self.assertEqual(utype, 'series')                # stype si no hay mapa


class TestCatGrpExtraAno(unittest.TestCase):
    """_cat_grp_extra: wants -> genre=año (y pedido o _YR_OPTS[0])."""

    def setUp(self):
        self._grp, self._yr = dict(C._GRP), dict(C._YR_OPTS)
        C._GRP['sg_tst_yr'] = collections.OrderedDict(
            [('Vivo', ('movie', 'real1', True, None))])
        C._YR_OPTS['sg_tst_yr'] = ['2025', '2024']

    def tearDown(self):
        C._GRP.clear(); C._GRP.update(self._grp)
        C._YR_OPTS.clear(); C._YR_OPTS.update(self._yr)

    def test_wants_year_pedido(self):
        ut, real, extra = C._cat_grp_extra('sg_tst_yr', 'year=1999')
        self.assertIn('genre=1999', extra)

    def test_wants_sin_year_usa_opts0(self):
        ut, real, extra = C._cat_grp_extra('sg_tst_yr', None)
        self.assertIn('genre=2025', extra)               # el más reciente


class TestSynthRecom(unittest.TestCase):
    """_synth_recom: cache hit no llama a trakt; miss -> _recom + _cset."""

    def setUp(self):
        import conflux.core.state as st
        st._cat_c.clear()

    def test_miss_llama_recom_y_cachea(self):
        import conflux.core.state as st
        with patch.object(C, '_recom', return_value={'metas': [{'id': 'x'}]}) as r:
            out, _, _ = C._synth_recom('movie', None, None, 'k')
        r.assert_called_once_with('movie')
        self.assertIsNotNone(st._cget(st._cat_c, ('recom', 'movie')))

    def test_hit_no_llama(self):
        import conflux.core.state as st
        st._cset(st._cat_c, ('recom', 'movie'), {'metas': [{'id': 'c'}]})
        with patch.object(C, '_recom') as r:
            out, _, _ = C._synth_recom('movie', None, None, 'k')
        r.assert_not_called()
        self.assertEqual(out['metas'][0]['id'], 'c')


class TestSynthCal(unittest.TestCase):
    """_synth_cal: cache por meses; <20 metas no cachea; None -> metas []."""

    def setUp(self):
        import conflux.core.state as st
        st._cat_c.clear()

    def test_hit_no_rebuild(self):
        import conflux.core.state as st
        st._cset(st._cat_c, ('estrenoscal', 2), {'metasDetailed': [{}] * 25})
        with patch.object(C, '_estrenos_cal') as ec:
            out, _, _ = C._synth_cal('series', None, None, 'k')
        ec.assert_not_called()

    def test_menos_de_20_no_cachea(self):
        import conflux.core.state as st
        with patch.object(C, '_estrenos_cal',
                          return_value={'metasDetailed': [{}] * 19}):
            C._synth_cal('series', None, None, 'k')
        self.assertIsNone(st._cget(st._cat_c, ('estrenoscal', 2)))

    def test_20_si_cachea(self):
        import conflux.core.state as st
        with patch.object(C, '_estrenos_cal',
                          return_value={'metasDetailed': [{}] * 20}):
            C._synth_cal('series', None, None, 'k')
        self.assertIsNotNone(st._cget(st._cat_c, ('estrenoscal', 2)))

    def test_none_a_metas_vacio(self):
        with patch.object(C, '_estrenos_cal', return_value=None):
            out, _, _ = C._synth_cal('series', None, None, 'k')
        self.assertEqual(out, {'metas': []})


class TestSynthM3u(unittest.TestCase):
    """_synth_m3u: date -> guía EPG del día; sin date -> lista canales."""

    def test_sin_date_lista(self):
        with patch.object(C, '_m3u_metas', return_value=[{'id': 'ch1'}]):
            self.assertEqual(C._synth_m3u(None), {'metas': [{'id': 'ch1'}]})

    def test_con_date_epg(self):
        with patch.object(C, '_m3u_metas', return_value=[{'id': 'ch1'}]), \
             patch.object(C, 'epg_metas', return_value={'metasDetailed': [{'id': 'e'}]}) as ep:
            out = C._synth_m3u('date=2024-01-02')
        ep.assert_called_once()
        self.assertEqual(out['metasDetailed'][0]['id'], 'e')


class TestXlateIdsMas(unittest.TestCase):
    """_cat_xlate_ids: cap optionsLimit=100; ambas claves traducen."""

    def test_cap_100_ids(self):
        extra = 'calendarVideosIds=' + ','.join(f'tt{i:07d}' for i in range(150))
        with patch.object(C, '_resolve_sid', side_effect=lambda u, i: i):
            out = C._cat_xlate_ids('movie', extra)
        ids = dict(__import__('urllib.parse', fromlist=['parse_qsl'])
                   .parse_qsl(out))['calendarVideosIds'].split(',')
        self.assertEqual(len(ids), 100)

    def test_lastvideosids_tambien(self):
        with patch.object(C, '_resolve_sid', side_effect=lambda u, i: 'tt9') as rs:
            out = C._cat_xlate_ids('series', 'lastVideosIds=kitsu:1,kitsu:2')
        self.assertEqual(rs.call_count, 2)
        self.assertIn('lastVideosIds=tt9%2Ctt9', out.replace(',', '%2C'))


class TestCatResolve(unittest.TestCase):
    """_cat_resolve: _GRP -> grp_extra; _SYNTH con respuesta -> dict directo."""

    def setUp(self):
        self._grp = dict(C._GRP)

    def tearDown(self):
        C._GRP.clear(); C._GRP.update(self._grp)

    def test_grp_usa_grp_extra(self):
        import conflux.core.state as st
        cid = f"s{st.SOURCES[0].name.lower()}_movie_grp"
        C._GRP[cid] = collections.OrderedDict(
            [('A', ('movie', 'r1', False, None))])
        with patch.object(C, '_cat_grp_extra',
                          return_value=('movie', 'r1', 'genre=A')) as ge:
            base, utype, real, extra = C._cat_resolve(
                cid, 'movie', 'genre=A', 'k')
        ge.assert_called_once()
        self.assertEqual(real, 'r1')

    def test_synth_con_respuesta_devuelve_dict(self):
        mf._SYNTH['suniform_movie_search'] = ('movie', '_busqueda', None)
        with patch.object(C, '_busqueda', return_value={'metas': []}):
            r = C._cat_resolve('suniform_movie_search', 'movie', 'search=x', 'k')
        self.assertEqual(r, {'metas': []})


class TestCatGenreOk(unittest.TestCase):
    """_cat_genre_ok: solo False cuando el manifest declara options y la
    pedida no está viva."""

    def setUp(self):
        import conflux.core.state as st
        self._man = list(st.manifests)
        self._dead = set(st._DEAD_OPT)
        st.manifests[:] = [{'catalogs': [
            {'id': 'iptv-all', 'type': 'tv',
             'extra': [{'name': 'genre', 'options': ['auto', 'sports']}]}]}] + \
            [None] * (len(st.SOURCES) - 1)

    def tearDown(self):
        import conflux.core.state as st
        st.manifests[:] = self._man
        st._DEAD_OPT.clear(); st._DEAD_OPT.update(self._dead)

    def test_base_desconocida_true(self):
        self.assertTrue(C._cat_genre_ok('http://nohost', 'tv', 'iptv-all', 'x'))

    def test_manifest_ausente_true(self):
        import conflux.core.state as st
        base = st.SOURCES[1].base
        self.assertTrue(C._cat_genre_ok(base, 'tv', 'iptv-all', 'x'))

    def test_sin_options_true(self):
        import conflux.core.state as st
        base = st.SOURCES[0].base
        self.assertTrue(C._cat_genre_ok(base, 'tv', 'otro-cat', 'x'))

    def test_opcion_muerta_false(self):
        import conflux.core.state as st
        base = st.SOURCES[0].base
        cid = f"s{st.SOURCES[0].name.lower()}_tv_iptv-all"
        st._DEAD_OPT.add((cid, 'sports'))
        self.assertFalse(C._cat_genre_ok(base, 'tv', 'iptv-all', 'sports'))
        self.assertTrue(C._cat_genre_ok(base, 'tv', 'iptv-all', 'auto'))

    def test_manifest_mas_corto_que_fuentes(self):
        """i == len(manifests) (borde >=): la guarda impide indexar fuera."""
        import conflux.core.state as st
        st.manifests[:] = [None]                            # len 1 < len(SOURCES)
        base = st.SOURCES[1].base                           # i=1: justo el borde
        self.assertTrue(C._cat_genre_ok(base, 'tv', 'x', 'g'))


class TestGrpFailover(unittest.TestCase):
    """_grp_failover: d con metas -> d; miembros en orden, genre reasignado;
    último recurso = último dict vacío."""

    def setUp(self):
        self._grp, self._yr = dict(C._GRP), dict(C._YR_OPTS)
        C._GRP['sg_tst_fo'] = collections.OrderedDict(
            [('A', ('movie', 'dead1', False, None)),
             ('B', ('movie', 'alive', True, None)),
             ('C', ('movie', 'alive', False, 'g2'))])   # dup real: dedup seen
        C._YR_OPTS['sg_tst_fo'] = ['2025']

    def tearDown(self):
        C._GRP.clear(); C._GRP.update(self._grp)
        C._YR_OPTS.clear(); C._YR_OPTS.update(self._yr)

    def test_d_con_metas_no_failover(self):
        d = {'metas': [{'id': 'x'}]}
        self.assertIs(C._grp_failover('sg_tst_fo', 'http://b', 'dead1', {}, d), d)

    def test_d_solo_metasdetailed_no_failover(self):
        d = {'metasDetailed': [{'id': 'x'}]}
        self.assertIs(C._grp_failover('sg_tst_fo', 'http://b', 'dead1', {}, d), d)

    def test_sirve_primer_vivo(self):
        calls = []
        def fake_fetch(url, t):
            calls.append(url)
            return {'metas': [{'id': 'ok'}]} if 'alive' in url else {'metas': []}
        with patch.object(C, 'fetch', side_effect=fake_fetch):
            d = C._grp_failover('sg_tst_fo', 'http://b', 'dead1', {}, {'metas': []})
        self.assertEqual(d['metas'][0]['id'], 'ok')
        self.assertEqual(len(calls), 1)              # dedup: 'alive' probado 1 vez
        self.assertIn('genre=2025', calls[0])        # wants -> _YR_OPTS[0]

    def test_todos_vacios_ultimo_dict(self):
        with patch.object(C, 'fetch', return_value={'metas': []}):
            d = C._grp_failover('sg_tst_fo', 'http://b', 'dead1', {}, None)
        self.assertEqual(d, {'metas': []})           # último 200-vacío gana a None


class TestCatNorm(unittest.TestCase):
    """_cat_norm: metas/metasDetailed no-lista -> []."""

    def test_metas_no_lista_vacia(self):
        d = {'metas': 'basura', 'metasDetailed': None}
        C._cat_norm(d)
        self.assertEqual(d['metas'], [])
        self.assertEqual(d['metasDetailed'], [])

    def test_metas_filtra_no_dicts(self):
        d = {'metas': [{'id': 'x'}, 'str', 3, {'id': 'y'}]}
        C._cat_norm(d)
        self.assertEqual(d['metas'], [{'id': 'x'}, {'id': 'y'}])


class TestCatServe(unittest.TestCase):
    """_cat_serve: no-dict -> stale; empty marca _DEAD_OPT; solo no-vacío cachea."""

    def setUp(self):
        import conflux.core.state as st
        self._dead = set(st._DEAD_OPT)
        st._cat_c.clear()

    def tearDown(self):
        import conflux.core.state as st
        st._DEAD_OPT.clear(); st._DEAD_OPT.update(self._dead)
        st._cat_c.clear()

    def test_no_dict_stale_o_vacio(self):
        with patch.object(C, '_cstale', return_value=None):
            self.assertEqual(C._cat_serve('c', 'k', None, True, None, False, None),
                             {'metas': []})
        with patch.object(C, '_cstale', return_value={'metas': [{'id': 's'}]}):
            self.assertEqual(C._cat_serve('c', 'k', None, True, None, False, None)
                             ['metas'][0]['id'], 's')

    def test_empty0_req_label_marca_muerta(self):
        import conflux.core.state as st
        C._cat_serve('cid1', 'k', {'metas': []}, True, 'Top', True, 'genre=Top')
        self.assertIn(('cid1', 'top'), st._DEAD_OPT)

    def test_empty_sin_extra_marca_base(self):
        import conflux.core.state as st
        C._cat_serve('cid2', 'k', {'metas': []}, True, None, False, None)
        self.assertIn(('cid2', ''), st._DEAD_OPT)

    def test_no_vacio_cachea_vacio_no(self):
        import conflux.core.state as st
        with patch.object(C, '_fix_metas', side_effect=lambda d: None), \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C._cat_serve('cid3', 'k1', {'metas': [{'id': 'x'}]}, False, None, False, None)
            C._cat_serve('cid3', 'k2', {'metas': []}, True, None, False, None)
        self.assertIsNotNone(st._cget(st._cat_c, 'k1'))
        self.assertIsNone(st._cget(st._cat_c, 'k2'))


class TestHandleCatalogBordes(unittest.TestCase):
    """handle_catalog: req_only_g solo con genre único; cache hit -> _rpdb;
    resolve error -> metas []; _GRP+empty0 -> failover."""

    def setUp(self):
        import conflux.core.state as st
        self._dead = set(st._DEAD_OPT)
        self._grp = dict(C._GRP)
        st._cat_c.clear()

    def tearDown(self):
        import conflux.core.state as st
        st._DEAD_OPT.clear(); st._DEAD_OPT.update(self._dead)
        C._GRP.clear(); C._GRP.update(self._grp)
        st._cat_c.clear()

    def test_genre_solo_empty_marca_label(self):
        import conflux.core.state as st
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'top', 'genre=Top')), \
             patch.object(C, 'fetch', return_value={'metas': []}), \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C.handle_catalog('movie', 'sfoo_movie_top', 'genre=Top')
        self.assertIn(('sfoo_movie_top', 'top'), st._DEAD_OPT)

    def test_genre_mas_filtros_no_marca_label(self):
        import conflux.core.state as st
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'top', 'genre=Top&skip=0')), \
             patch.object(C, 'fetch', return_value={'metas': []}), \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C.handle_catalog('movie', 'sfoo_movie_top', 'genre=Top&skip=0')
        self.assertNotIn(('sfoo_movie_top', 'top'), st._DEAD_OPT)

    def test_cache_hit_rpdb(self):
        import conflux.core.state as st
        st._cset(st._cat_c, ('movie', 'cid', None), {'metas': [{'id': 'c'}]})
        with patch.object(C, '_rpdb', side_effect=lambda d: d) as rp:
            out = C.handle_catalog('movie', 'cid', None)
        rp.assert_called_once()
        self.assertEqual(out['metas'][0]['id'], 'c')

    def test_resolve_error_metas_vacio(self):
        with patch.object(C, '_cat_resolve', side_effect=ValueError):
            self.assertEqual(C.handle_catalog('movie', 'bad', None), {'metas': []})

    def test_grp_empty_llama_failover(self):
        C._GRP['sfoo_movie_g'] = collections.OrderedDict(
            [('A', ('movie', 'r1', False, None))])
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'r1', None)), \
             patch.object(C, 'fetch', return_value=None), \
             patch.object(C, '_grp_failover', return_value={'metas': []}) as fo, \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C.handle_catalog('movie', 'sfoo_movie_g', None)
        fo.assert_called_once()

    def test_no_grp_empty_sin_failover(self):
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'top', None)), \
             patch.object(C, 'fetch', return_value=None), \
             patch.object(C, '_grp_failover') as fo, \
             patch.object(C, '_cstale', return_value=None):
            C.handle_catalog('movie', 'sfoo_movie_top', None)
        fo.assert_not_called()


class TestBusquedaBordes(unittest.TestCase):
    """_busqueda fino: utype tal cual, early-done por tiempo y por volumen."""

    def _tgts(self, n):
        return [('http://b', 'movie', f'c{i}') for i in range(n)]

    def _run(self, tgts, metas_por, times):
        n = [0]
        def ff(url, t):
            n[0] += 1
            return {'metas': [{'id': f'tt{n[0]:07d}x{j}'} for j in range(metas_por)]}
        faketime = type('T', (), {'time': staticmethod(lambda: next(times))})
        with patch.object(C, '_search_targets', return_value=tgts) as tg, \
             patch.object(C, 'fetch', side_effect=ff), \
             patch.object(C, 'time', faketime), \
             patch.object(C, '_POOL') as pool:
            pool.submit.side_effect = lambda *a: __import__('concurrent.futures',
                fromlist=['Future']).Future()
            futs = []
            def submit(fn, *a):
                f = __import__('concurrent.futures', fromlist=['Future']).Future()
                f.set_result(fn(*a))
                futs.append(f)
                return f
            pool.submit.side_effect = submit
            pool.map.side_effect = lambda fn, it: [fn(x) for x in it]
            out = C._busqueda('movie', 'search=x')
        return out, tg

    def test_utype_no_anime_tal_cual(self):
        out, tg = self._run(self._tgts(1), 1, iter([0.0] * 5))
        tg.assert_called_once_with('movie')          # 'anime' solo si utype=='anime'

    def test_deadline_10s(self):
        """as_completed recibe timeout=10: el presupuesto total de búsqueda."""
        with patch.object(C, '_search_targets', return_value=self._tgts(1)), \
             patch.object(C, 'fetch', return_value={'metas': []}), \
             patch.object(C, 'as_completed', return_value=iter(())) as ac:
            C._busqueda('movie', 'search=x')
        self.assertEqual(ac.call_args[1].get('timeout',
                         ac.call_args[0][1] if len(ac.call_args[0]) > 1 else None), 10)

    def test_early_done_por_volumen(self):
        """el>4 y >=40 metas -> break: con 8 fuentes se detiene a los 40."""
        out, _ = self._run(self._tgts(8), 10, iter([0.0] + [5.0] * 12))
        self.assertEqual(len(out['metas']), 40)

    def test_el_borde_4_no_basta(self):
        """el==4 no cumple >4: el corte llega por stragglers (restan <=3)."""
        out, _ = self._run(self._tgts(8), 10, iter([0.0] + [4.0] * 12))
        self.assertEqual(len(out['metas']), 50)      # break en done=5 (quedan 3)

    def test_el_borde_2_no_basta(self):
        """el==2 no cumple >2 en el corte por stragglers: se espera a todos."""
        out, _ = self._run(self._tgts(8), 10, iter([0.0] + [2.0] * 12))
        self.assertEqual(len(out['metas']), 80)

    def test_elapsed_es_relativo(self):
        """el = ahora - t0: con t0=10 y ahora=11 no hay corte temprano."""
        out, _ = self._run(self._tgts(8), 10, iter([10.0] + [11.0] * 12))
        self.assertEqual(len(out['metas']), 80)


class TestSearchMergeBordes(unittest.TestCase):
    """_search_merge: solo dicts con id entran (dedup por clave canónica)."""

    def test_sin_id_no_dict_fuera(self):
        seen, out = set(), []
        with patch.object(C._POOL, 'map',
                          side_effect=lambda fn, it: [fn(x) for x in it]):
            C._search_merge({'metas': [{'id': 'tt1'}, {'nombre': 'x'},
                                       'str', {'id': ''}]}, 'movie', seen, out)
        self.assertEqual(out, [{'id': 'tt1'}])


class TestFailoverFino(unittest.TestCase):
    """_grp_failover: el PRIMER miembro vivo gana (no el último), None no rompe,
    timeout fetch=7."""

    def setUp(self):
        self._grp, self._yr = dict(C._GRP), dict(C._YR_OPTS)
        C._GRP['sg_tst_f2'] = collections.OrderedDict(
            [('A', ('movie', 'dead1', False, None)),
             ('B', ('movie', 'b1', True, None)),
             ('C', ('movie', 'b2', False, None))])
        C._YR_OPTS['sg_tst_f2'] = ['2025']

    def tearDown(self):
        C._GRP.clear(); C._GRP.update(self._grp)
        C._YR_OPTS.clear(); C._YR_OPTS.update(self._yr)

    def test_primer_vivo_gana_y_no_sigue(self):
        calls = []
        def ff(url, t):
            calls.append(url)
            return {'metas': [{'id': 'B' if 'b1' in url else 'C'}]}
        with patch.object(C, 'fetch', side_effect=ff):
            d = C._grp_failover('sg_tst_f2', 'http://b', 'dead1', {}, {'metas': []})
        self.assertEqual(d['metas'][0]['id'], 'B')      # primer vivo, no el último
        self.assertEqual(len(calls), 1)                 # b2 ni se consulta

    def test_none_luego_vivo(self):
        seq = iter([None, {'metas': [{'id': 'ok'}]}])
        with patch.object(C, 'fetch', side_effect=lambda u, t: next(seq)):
            d = C._grp_failover('sg_tst_f2', 'http://b', 'dead1', {}, None)
        self.assertEqual(d['metas'][0]['id'], 'ok')

    def test_d_vivo_no_reevalua(self):
        d = {'metas': [{'id': 'x'}]}
        with patch.object(C, 'fetch', return_value={'metas': [{'id': 'y'}]}):
            self.assertIs(C._grp_failover('sg_tst_f2', 'http://b', 'dead1', {}, d), d)

    def test_timeout_fetch_7(self):
        with patch.object(C, 'fetch', return_value=None) as f:
            C._grp_failover('sg_tst_f2', 'http://b', 'dead1', {}, {'metas': []})
        self.assertEqual(f.call_args[0][1], 7)


class TestHandleCatalogFino(unittest.TestCase):
    """handle_catalog fino: empty0 distingue no-dict de sin-metas; kv llega
    completo al failover; fetch timeout=7; no-vacío sin extra no marca."""

    def setUp(self):
        import conflux.core.state as st
        self._dead = set(st._DEAD_OPT)
        self._grp = dict(C._GRP)
        st._cat_c.clear()

    def tearDown(self):
        import conflux.core.state as st
        st._DEAD_OPT.clear(); st._DEAD_OPT.update(self._dead)
        C._GRP.clear(); C._GRP.update(self._grp)
        st._cat_c.clear()

    def test_grp_no_vacio_sin_failover(self):
        cid = f"s{st_name()}_movie_g"
        C._GRP[cid] = collections.OrderedDict(
            [('A', ('movie', 'r1', False, None))])
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'r1', None)), \
             patch.object(C, 'fetch', return_value={'metas': [{'id': 'x'}]}), \
             patch.object(C, '_grp_failover') as fo, \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C.handle_catalog('movie', cid, None)
        fo.assert_not_called()                          # metas -> empty0 False

    def test_fetch_none_sigue_a_failover_o_vacio(self):
        cid = f"s{st_name()}_movie_g"
        C._GRP[cid] = collections.OrderedDict(
            [('A', ('movie', 'r1', False, None))])
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'r1', None)), \
             patch.object(C, 'fetch', return_value=None), \
             patch.object(C, '_grp_failover', return_value={'metas': []}) as fo, \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C.handle_catalog('movie', cid, None)
        fo.assert_called_once()                         # no-dict = vacío: failover

    def test_kv_extra_llega_a_failover(self):
        cid = f"s{st_name()}_movie_g"
        C._GRP[cid] = collections.OrderedDict(
            [('A', ('movie', 'r1', False, None))])
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'r1', 'genre=Top')), \
             patch.object(C, '_cat_fix_extra', return_value='genre=Top'), \
             patch.object(C, 'fetch', return_value=None), \
             patch.object(C, '_grp_failover', return_value={'metas': []}) as fo, \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C.handle_catalog('movie', cid, 'genre=Top')
        self.assertEqual(fo.call_args[0][3].get('genre'), 'Top')

    def test_serve_fetch_timeout_7(self):
        with patch.object(C, '_cat_resolve',
                          return_value=('http://b', 'movie', 'top', None)), \
             patch.object(C, 'fetch', return_value={'metas': [{'id': 'x'}]}) as f, \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C.handle_catalog('movie', f"s{st_name()}_movie_top", None)
        self.assertEqual(f.call_args[0][1], 7)

    def test_no_vacio_sin_extra_no_marca(self):
        import conflux.core.state as st
        with patch.object(C, '_fix_metas'), \
             patch.object(C, '_rpdb', side_effect=lambda d: d):
            C._cat_serve('cid9', 'k', {'metas': [{'id': 'x'}]},
                         False, None, False, None)
        self.assertNotIn(('cid9', ''), st._DEAD_OPT)


def st_name():
    import conflux.core.state as st
    return st.SOURCES[0].name.lower()
