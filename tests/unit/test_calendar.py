# pylint: disable=missing-docstring,protected-access,import-outside-toplevel
"""Unit tests conflux.domain.calendar — helpers del feed de estrenos."""
import unittest
from unittest.mock import MagicMock, patch

import conflux.core.state as st
import conflux.domain.calendar as CAL


class TestAddFilm(unittest.TestCase):
    def test_futuro_anade_con_video_estreno(self):
        seen, out = set(), []
        CAL._cal_add_film({'id': 'x', 'imdb_id': 'tt9', 'name': 'P',
                           'released': '2099-05-01'}, '2020-01-01', seen, out)
        self.assertEqual(len(out), 1)
        m = out[0]
        self.assertEqual(m['id'], 'tt9')
        self.assertEqual(m['type'], 'series')
        self.assertEqual(m['videos'][0]['id'], 'tt9:1:1')

    def test_pasado_o_sin_fecha_fuera(self):
        seen, out = set(), []
        CAL._cal_add_film({'id': 'tt1', 'released': '2000-01-01'},
                          '2020-01-01', seen, out)
        CAL._cal_add_film({'id': 'tt2'}, '2020-01-01', seen, out)
        self.assertEqual(out, [])

    def test_dup_y_malformado_fuera(self):
        seen, out = {'tt1'}, []
        CAL._cal_add_film({'id': 'tt1', 'released': '2099-01-01'},
                          '2020-01-01', seen, out)
        CAL._cal_add_film(None, '2020-01-01', seen, out)
        CAL._cal_add_film('no dict', '2020-01-01', seen, out)
        self.assertEqual(out, [])


class TestEnsureMeta(unittest.TestCase):
    def test_ya_presente_devuelve_existente(self):
        metas = {'tt1': {'id': 'tt1', 'videos': []}}
        seen, out = set(), []
        m = CAL._cal_ensure_meta('tt1', {'id': 'otro'}, metas, seen, out)
        self.assertIs(m, metas['tt1'])
        self.assertEqual(out, [])                      # no duplica

    def test_nuevo_registra(self):
        metas, seen, out = {}, set(), []
        m = CAL._cal_ensure_meta('tt5', {'id': 'orig', 'name': 'N'},
                                 metas, seen, out)
        self.assertEqual(m['id'], 'tt5')
        self.assertIn('tt5', seen)
        self.assertIs(out[0], m)
        self.assertEqual(m['videos'], [])

    def test_sin_meta_none(self):
        metas, seen, out = {}, set(), []
        self.assertIsNone(CAL._cal_ensure_meta('tt1', None, metas, seen, out))
        self.assertIsNone(CAL._cal_ensure_meta('tt1', {}, metas, seen, out))


class TestAnimeVideoYFill(unittest.TestCase):
    def test_video_formato_episodio(self):
        a = {'ep': 7, 'title': 'El ep', 'airingAt': 1_800_000_000, 'img': 'i'}
        v = CAL._cal_anime_video('tt1', a, {'poster': 'p'})
        self.assertEqual(v['id'], 'tt1:1:7')
        self.assertEqual(v['episode'], 7)
        self.assertEqual(v['thumbnail'], 'i')
        self.assertTrue(v['released'].endswith('.000Z'))

    def test_video_sin_titulo_nombre_default(self):
        a = {'ep': 3, 'airingAt': 1_800_000_000}
        v = CAL._cal_anime_video('tt1', a, {'poster': 'p'})
        self.assertEqual(v['name'], 'Episodio 3')
        self.assertEqual(v['thumbnail'], 'p')          # poster del meta

    def test_fill_solo_faltan(self):
        metas = {}
        seen, out = set(), []
        m = CAL._cal_ensure_meta('tt1', {'id': 'x',
                                         'videos': [{'id': 'tt1:1:5'}]},
                                 metas, seen, out)
        eps = [{'ep': 5, 'airingAt': 1}, {'ep': 6, 'airingAt': 1}]
        CAL._cal_anime_fill('tt1', eps, {'id': 'x'}, metas, seen, out)
        self.assertEqual([v['id'] for v in m['videos']], ['tt1:1:5', 'tt1:1:6'])

    def test_fill_sin_meta_no_crea(self):
        metas, seen, out = {}, set(), []
        CAL._cal_anime_fill('tt1', [{'ep': 1, 'airingAt': 1}], None,
                            metas, seen, out)
        self.assertEqual(out, [])


class TestAnimeEpsMap(unittest.TestCase):
    def test_resuelve_mal_a_tt_y_agrupa(self):
        al = [{'idMal': 1, 'ep': 1}, {'idMal': 1, 'ep': 2},
              {'idMal': 2, 'ep': 1}]
        with patch.object(CAL, '_resolve_sid',
                          side_effect=['tt100', 'tt100', 'kitsu:x']), \
                patch.object(CAL, '_POOL_BG') as pool:
            pool.map = lambda f, it: [f(i) for i in it]
            by = CAL._anime_eps_map(al)
        self.assertEqual(len(by['tt100']), 2)          # mismo tt agrupa eps
        self.assertNotIn('kitsu:x', by)                # no-tt fuera


class TestCalAnimeEps(unittest.TestCase):
    def test_fill_por_episodio(self):
        eps_by_tt = {'tt1': [{'ep': 1, 'airingAt': 1_800_000_000}]}
        fut = MagicMock()
        fut.result.return_value = {'meta': {'id': 'x', 'name': 'A'}}
        with patch.object(CAL, '_anime_eps_map', return_value=eps_by_tt), \
                patch.object(CAL, '_anilist_airing', return_value=[{}]), \
                patch.object(CAL, '_POOL_BG') as pool:
            pool.submit.return_value = fut
            pool.map = lambda f, it: []
            out, seen = [], set()
            CAL._cal_anime_eps('http://cine', 7, seen, out)
        self.assertEqual(out[0]['id'], 'tt1')
        self.assertEqual(out[0]['videos'][0]['id'], 'tt1:1:1')


class TestMyEps(unittest.TestCase):
    def test_videos_en_tu_trakt(self):
        m = {'id': 'tt1', 'videos': [], 'poster': 'p'}
        metas = {'tt1': m}
        my_eps = [('tt1', 2, 3, '2030-01-01', {})]
        CAL._my_eps_videos(metas, my_eps)
        self.assertEqual(m['videos'][0]['id'], 'tt1:2:3')
        self.assertIn('en tu Trakt', m['videos'][0]['name'])
        CAL._my_eps_videos(metas, my_eps)              # dedup: no duplica
        self.assertEqual(len(m['videos']), 1)

    def test_meta_ausente_no_explota(self):
        CAL._my_eps_videos({}, [('ttX', 1, 1, '2030', {})])

    def test_my_fill_fallback_trakt(self):
        fut = MagicMock()
        fut.result.return_value = {}                   # cinemeta no tiene el tt
        with patch.object(CAL, '_POOL_BG') as pool:
            pool.submit.return_value = fut
            metas, seen, out = {}, set(), []
            my_eps = [('tt2', 1, 1, '2030', {'id': 'x', 'name': 'F'})]
            CAL._cal_my_fill('http://c', ['tt2'], my_eps, metas, seen, out)
        self.assertEqual(out[0]['id'], 'tt2')          # meta fallback usado

    def test_my_eps_fill_endtoend(self):
        fut = MagicMock()
        fut.result.return_value = {'meta': {'id': 'c', 'name': 'S'}}
        with patch.object(CAL, '_POOL_BG') as pool:
            pool.submit.return_value = fut
            out, seen = [], set()
            my_eps = [('tt9', 1, 2, '2030-01-01', {})]
            CAL._cal_my_eps('http://c', my_eps, seen, out)
        self.assertEqual(out[0]['id'], 'tt9')
        self.assertEqual(out[0]['videos'][0]['id'], 'tt9:1:2')


class _F:
    """Future mínimo con .result()."""
    def __init__(self, val):
        self._v = val

    def result(self):
        if isinstance(self._v, Exception):
            raise self._v
        return self._v


class _Pool:
    """Pool síncrono: submit despacha por identidad de función."""
    def __init__(self, canned):
        self.canned = canned          # url-contains -> payload
        self.submitted = []

    def submit(self, fn, *a):
        if a and isinstance(a[0], str) and a[0].startswith('http'):
            self.submitted.append(a[0])
            for k, v in self.canned.items():
                if k in a[0]:
                    return _F(v)
            return _F({})
        try:
            return _F(fn(*a))
        except Exception as e:                        # noqa: BLE001 — future real
            return _F(e)

    def map(self, f, it):
        return [f(x) for x in it]


class TestAddFilmBorde(unittest.TestCase):
    def test_released_igual_hoy_no_es_futuro(self):
        """rel == hoy -> fuera: `rel <= hoy` no es `<` y `[:10]` no `[:11]`
        (LtE/10+1@23-24). Input con hora larga separa ambos mutantes."""
        seen, out = set(), []
        CAL._cal_add_film({'id': 'tt1', 'released': '2020-01-02T23:00:00Z'},
                          '2020-01-02', seen, out)
        self.assertEqual(out, [])

    def test_overview_descripcion(self):
        """videos[0].overview = description del meta (Or@32)."""
        seen, out = set(), []
        CAL._cal_add_film({'id': 'tt1', 'released': '2099-01-01',
                           'description': 'Una sinopsis'}, '2020-01-01', seen, out)
        self.assertEqual(out[0]['videos'][0]['overview'], 'Una sinopsis')


class TestBase(unittest.TestCase):
    def test_base_por_nombre(self):
        """_base('Cinemeta') devuelve SU base, no la primera (Eq@38)."""
        from conflux.core.state import SOURCES
        esperado = next(b for n, b in SOURCES if n == 'Cinemeta')
        self.assertEqual(CAL._base('Cinemeta'), esperado)


class TestFres(unittest.TestCase):
    def test_fallo_devuelve_default(self):
        """_fres(fallo) -> [] por defecto (IsNot@78: mutant daba None)."""
        self.assertEqual(CAL._fres(_F(RuntimeError('x'))), [])
        self.assertEqual(CAL._fres(_F(RuntimeError('x')), 'd'), 'd')
        self.assertEqual(CAL._fres(_F([1])), [1])


class TestCatIds(unittest.TestCase):
    def test_prefijo_y_solo_dicts(self):
        """Solo dicts con id del prefijo (Or@68, And@69: el no-dict rompería
        `m.get` si `and` fuera `or`)."""
        fut = _F({'metas': [{'id': 'tt1'}, 'no-dict', {'id': 'kitsu:1'},
                            {'id': 'tt2'}]})
        self.assertEqual(CAL._cat_ids(fut, 'tt'), ['tt1', 'tt2'])
        self.assertEqual(CAL._cat_ids(fut), ['tt1', 'kitsu:1', 'tt2'])

    def test_metas_none_vacio(self):
        self.assertEqual(CAL._cat_ids(_F({'metas': None})), [])


class TestSeedIds(unittest.TestCase):
    def test_urls_ano_actual_y_siguiente(self):
        """Se piden géneros del año actual y el siguiente + skip 50/100
        (Add@50/51, 50+1/100+1@51)."""
        pool = _Pool({})
        with patch.object(CAL, '_POOL_BG', pool), \
                patch.object(CAL, 'tmdb_year', return_value='http://t/y'):
            CAL._cal_seed_ids('http://c', 'http://k', 'http://a')
        y = int(CAL._YEARS[0])
        urls = ' '.join(pool.submitted)
        self.assertIn(f'genre={y}', urls)
        self.assertIn(f'genre={y + 1}', urls)
        self.assertIn('skip=50', urls)
        self.assertIn('skip=100', urls)

    def test_top_ids_dedup_cap_y_resolucion(self):
        """top[:30] + temporada dedup + kitsu->tt solo tt (NotIn@59/62,
        And@62, 30+1/20+1@58-60)."""
        canned = {
            'series/top.json': {'metas': [{'id': f'tt{i:03d}'} for i in range(35)]},
            'latest_anime_seasons': {'metas': [{'id': 'tt000'},  # dup -> fuera
                                               {'id': 'tt999'}]},
            'kitsu-anime-airing': {'metas': [{'id': f'kitsu:{i}'} for i in range(25)]},
        }
        pool = _Pool(canned)
        with patch.object(CAL, '_POOL_BG', pool), \
                patch.object(CAL, 'tmdb_year', return_value='http://t/y'), \
                patch.object(CAL, '_resolve_sid',
                             side_effect=lambda t, i: {'kitsu:0': 'tt88',
                                                       'kitsu:20': 'tt77'}.get(i, i)):
            top, _fs, _amv, _td = CAL._cal_seed_ids('http://c', 'http://k', 'http://a')
        self.assertEqual(len(top), 32)                   # 30 top + tt999 + tt88
        self.assertIn('tt999', top)
        self.assertEqual(top.count('tt000'), 1)          # dedup temporada
        self.assertIn('tt88', top)                       # kitsu:0 resuelto
        self.assertNotIn('tt77', top)                    # kitsu:20 fuera: cap [:20]
        self.assertFalse(any(x.startswith('kitsu') for x in top))


class TestSeriesEps(unittest.TestCase):
    def test_sin_top_ids_no_fetch(self):
        """Guarda `if not top_ids: return` — sin semilla no se toca la red
        (not-elim@91)."""
        with patch.object(CAL, 'fetch') as f:
            CAL._cal_series_eps('http://c', [], set(), [])
        f.assert_not_called()

    def test_cv_dedup_y_requiere_videos(self):
        """Solo metas con id nuevo y videos (NotIn/And@84, Or@83)."""
        seen, out = {'tt1'}, []
        cv = {'metasDetailed': [{'id': 'tt1', 'videos': [{'id': 'v'}]},  # dup
                                {'id': 'tt2'},                          # sin videos
                                {'id': 'tt3', 'videos': []},            # [] = falsy
                                {'id': 'tt4', 'videos': [{'id': 'v'}]}]}
        CAL._cal_series_eps_cv(cv, seen, out)
        self.assertEqual([m['id'] for m in out], ['tt4'])


class TestMetaFilms(unittest.TestCase):
    def test_meta_sin_imdb_conserva_id(self):
        """m sin imdb_id: el `if m and m.get('imdb_id')` NO pisa id
        (And@109 -> or -> KeyError)."""
        pool = _Pool({'meta/movie/ttX.json': {'meta': {'id': 'ttX',
                                                     'released': '2099-01-01'}}})
        seen, out = set(), []
        with patch.object(CAL, '_POOL_BG', pool):
            CAL._cal_meta_films('http://tmdb', ['ttX'], '2020-01-01', seen, out)
        self.assertEqual(out[0]['id'], 'ttX')

    def test_meta_con_imdb_canoniza(self):
        pool = _Pool({'meta/movie/ttX.json': {'meta': {'id': 'otro',
                                                     'imdb_id': 'ttY',
                                                     'released': '2099-01-01'}}})
        seen, out = set(), []
        with patch.object(CAL, '_POOL_BG', pool):
            CAL._cal_meta_films('http://tmdb', ['ttX'], '2020-01-01', seen, out)
        self.assertEqual(out[0]['id'], 'ttY')


class TestBareIds(unittest.TestCase):
    def test_bare_solo_sin_fecha_y_tt(self):
        """Bare = sin released[:10] y id tt (Or@124/125, And@125, not-elim)."""
        metas = [{'id': 'tt1'},                                    # sin released -> bare
                 {'id': 'tt2', 'released': '2025-01-01'},          # con fecha -> no
                 {'id': 'kitsu:1'},                                # no-tt -> no
                 {'id': 'tt3', 'released': ''}]                    # '' -> bare
        pool = _Pool({'bare_src': {'metas': metas}})
        f = _F({'metas': metas})
        with patch.object(CAL, '_POOL_BG', pool):
            self.assertEqual(CAL._cal_bare_ids([f], _F({'metas': []})),
                             ['tt1', 'tt3'])

    def test_bare_cap_40(self):
        metas = [{'id': f'tt{i:02d}'} for i in range(45)]
        with patch.object(CAL, '_POOL_BG', _Pool({})):
            self.assertEqual(len(CAL._cal_bare_ids([_F({'metas': metas})],
                                                 _F({'metas': []}))), 40)


class TestCalMyFillFallback(unittest.TestCase):
    def test_meta_cinemeta_gana_al_fallback(self):
        """meta upstream tiene prioridad sobre el show-meta de Trakt
        (Or@218 -> and usaría siempre el fallback)."""
        fut = _F({'meta': {'id': 'c', 'name': 'UPSTREAM'}})
        with patch.object(CAL, '_POOL_BG') as pool:
            pool.submit.return_value = fut
            metas, seen, out = {}, set(), []
            my_eps = [('tt2', 1, 1, '2030', {'id': 'x', 'name': 'FALLBACK'})]
            CAL._cal_my_fill('http://c', ['tt2'], my_eps, metas, seen, out)
        self.assertEqual(out[0]['name'], 'UPSTREAM')


class TestEstrenosCal(unittest.TestCase):
    def _pool(self, canned):
        return _Pool(canned)

    def test_dias_meses_por_31(self):
        """days = calendar_months_ahead * 31 (2+1/31+1/Mult@247)."""
        from tests import cfg_swap
        _, undo = cfg_swap(calendar_months_ahead=3)
        pool = self._pool({})
        try:
            with patch.object(CAL, '_POOL_BG', pool), \
                    patch.object(CAL, 'tmdb_year', return_value='http://t/y'), \
                    patch.object(CAL, '_anilist_airing',
                                 return_value=[]) as al, \
                    patch.object(CAL, '_trakt_movies', return_value=[]), \
                    patch.object(CAL, '_trakt_my', return_value=([], [])), \
                    patch.object(CAL, '_resolve_sid', side_effect=lambda t, i: i):
                CAL._estrenos_cal()
        finally:
            undo()
        # _anilist_airing va al pool como callable: el _Pool lo ejecuta
        al.assert_called_once_with(93)

    def test_dias_default_2_meses(self):
        """Sin calendar_months_ahead en CFG -> default 2*31=62 (2+1@247:
        el mutante solo difiere con la clave ausente)."""
        pool = self._pool({})
        old = st.CFG.pop('calendar_months_ahead', None)
        try:
            with patch.object(CAL, '_POOL_BG', pool), \
                    patch.object(CAL, 'tmdb_year', return_value='http://t/y'), \
                    patch.object(CAL, '_anilist_airing',
                                 return_value=[]) as al, \
                    patch.object(CAL, '_trakt_movies', return_value=[]), \
                    patch.object(CAL, '_trakt_my', return_value=([], [])), \
                    patch.object(CAL, '_resolve_sid', side_effect=lambda t, i: i):
                CAL._estrenos_cal()
        finally:
            if old is not None:
                st.CFG['calendar_months_ahead'] = old
        al.assert_called_once_with(62)

    def test_sin_top_ids_no_pide_calendar_videos(self):
        """top_ids vacío -> f_cv None y nunca se pide calendar-videos
        (IsNot@258: el mutante llamaría .result() sobre None)."""
        pool = self._pool({'series/top.json': {'metas': []}})
        with patch.object(CAL, '_POOL_BG', pool), \
                patch.object(CAL, 'tmdb_year', return_value='http://t/y'), \
                patch.object(CAL, '_anilist_airing', return_value=[]), \
                patch.object(CAL, '_trakt_movies', return_value=[]), \
                patch.object(CAL, '_trakt_my', return_value=([], [])), \
                patch.object(CAL, '_resolve_sid', side_effect=lambda t, i: i):
            out = CAL._estrenos_cal()
        self.assertIsNone(out)
        self.assertFalse(any('calendar-videos' in u for u in pool.submitted))

    def test_orden_proximo_estreno_primero(self):
        """Sort key: min released futuro; sin fechas futuras -> '9' al final
        (Or@264/265, Gt@266: pasados no cuentan como próximo estreno)."""
        hoy = __import__('time').strftime('%Y-%m-%d')
        canned = {
            'series/top.json': {'metas': [{'id': 'ttS'}]},
            'calendar-videos': {'metasDetailed': [
                {'id': 'ttS', 'videos': [{'id': 'ttS:1:1',
                                          'released': hoy}]}]},   # hoy no es futuro
            'genre=': {'metas': [{'id': 'ttF', 'released': '2999-06-15'},
                                 {'id': 'ttG', 'released': '2999-01-10'}]},
        }
        pool = self._pool(canned)
        with patch.object(CAL, '_POOL_BG', pool), \
                patch.object(CAL, 'tmdb_year', return_value='http://t/y'), \
                patch.object(CAL, '_anilist_airing', return_value=[]), \
                patch.object(CAL, '_trakt_movies', return_value=[]), \
                patch.object(CAL, '_trakt_my', return_value=([], [])), \
                patch.object(CAL, '_resolve_sid', side_effect=lambda t, i: i):
            out = CAL._estrenos_cal()
        ids = [m['id'] for m in out['metasDetailed']]
        # ttG (estreno más próximo) primero; ttS (released==hoy, no >) al final
        self.assertEqual(ids.index('ttG'), 0)
        self.assertLess(ids.index('ttF'), ids.index('ttS'))


if __name__ == '__main__':
    unittest.main()
