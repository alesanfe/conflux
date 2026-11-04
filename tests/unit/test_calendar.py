# pylint: disable=missing-docstring,protected-access,import-outside-toplevel
"""Unit tests conflux.domain.calendar — helpers del feed de estrenos."""
import unittest
from unittest.mock import MagicMock, patch

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


if __name__ == '__main__':
    unittest.main()
