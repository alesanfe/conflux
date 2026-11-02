# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.trakt — seeds, related."""
import unittest
from unittest.mock import patch

import conflux.infra.trakt as T


class TestRecomSeeds(unittest.TestCase):
    def test_extrae_slugs(self):
        hist = [{'movie': {'ids': {'trakt': 'dune-2021'}}},
                {'show': {'ids': {'trakt': 'breaking-bad'}}},
                {'movie': {'ids': {}}}]
        self.assertEqual(T._recom_seeds(hist), ['dune-2021', 'breaking-bad'])


if __name__ == '__main__':
    unittest.main()




from tests import cfg_swap, fake_session


class TestTraktHeaders(unittest.TestCase):
    def test_h_basico_y_auth(self):
        _, undo = cfg_swap(trakt_client_id='cid', trakt_access_token='tok')
        try:
            h = T._trakt_h()
            self.assertEqual(h['trakt-api-key'], 'cid')
            self.assertNotIn('Authorization', h)
            h = T._trakt_h(auth=True)
            self.assertEqual(h['Authorization'], 'Bearer tok')
        finally:
            undo()


class TestTraktMovies(unittest.TestCase):
    def test_sin_client_id_vacio(self):
        _, undo = cfg_swap(trakt_client_id='')
        try:
            self.assertEqual(T._trakt_movies(7), [])
        finally:
            undo()

    def test_estrenos_mapeados(self):
        _, undo = cfg_swap(trakt_client_id='cid')
        s, _ = fake_session({'json': [
            {'movie': {'ids': {'imdb': 'tt1234'}, 'title': 'Film', 'year': 2025},
             'released': '2025-06-01'},
            {'movie': {'ids': {}}, 'released': '2025-06-02'}]})  # sin imdb -> fuera
        try:
            with patch.object(T, '_session', return_value=s):
                out = T._trakt_movies(7)
        finally:
            undo()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['id'], 'tt1234')
        self.assertEqual(out[0]['released'], '2025-06-01T00:00:00.000Z')
        self.assertIn('poster', out[0])

    def test_upstream_roto_vacio(self):
        _, undo = cfg_swap(trakt_client_id='cid')
        s, _ = fake_session(RuntimeError('caído'))
        try:
            with patch.object(T, '_session', return_value=s):
                self.assertEqual(T._trakt_movies(7), [])
        finally:
            undo()


class TestTraktDevice(unittest.TestCase):
    def tearDown(self):
        T._TRAKT_DEV.clear()

    def test_device_start(self):
        _, undo = cfg_swap(trakt_client_id='cid')
        s, calls = fake_session({'json': {
            'device_code': 'dc', 'user_code': 'ABCD',
            'verification_url': 'https://trakt.tv/activate', 'expires_in': 600}})
        try:
            with patch.object(T, '_session', return_value=s), \
                 patch.object(T, '_trakt_device_poll'):
                d = T._trakt_device_start()
        finally:
            undo()
        self.assertEqual(d['user_code'], 'ABCD')
        self.assertEqual(T._TRAKT_DEV['device_code'], 'dc')
        self.assertEqual(calls[0][0], 'POST')

    def test_device_start_fallo(self):
        _, undo = cfg_swap(trakt_client_id='cid')
        s, _ = fake_session(__import__('requests').RequestException('x'))
        try:
            with patch.object(T, '_session', return_value=s):
                self.assertIsNone(T._trakt_device_start())
        finally:
            undo()

    def test_device_poll_exito(self):
        """Poll con token -> CFG actualizado + _TRAKT_DEV limpio."""
        _, undo = cfg_swap(trakt_client_id='c', trakt_client_secret='s',
                           trakt_access_token='', trakt_refresh_token='',
                           trakt_expires=0)
        T._TRAKT_DEV.update({'device_code': 'dc', 'interval': 0,
                             'deadline': __import__('time').time() + 60})
        s, _ = fake_session({'json': {'access_token': 'AT', 'refresh_token': 'RT',
                                      'expires_in': 90}})
        try:
            with patch.object(T, '_session', return_value=s), \
                 patch.object(T.time, 'sleep'), \
                 patch.object(T, '_save_cfg'):
                T._trakt_device_poll()
            self.assertEqual(T.CFG['trakt_access_token'], 'AT')
            self.assertFalse(T._TRAKT_DEV)
        finally:
            undo()

    def test_device_poll_expira(self):
        _, undo = cfg_swap()
        T._TRAKT_DEV.update({'deadline': 0})
        try:
            T._trakt_device_poll()
        finally:
            undo()
        self.assertFalse(T._TRAKT_DEV)


class TestTraktRefresh(unittest.TestCase):
    def test_sin_refresh_token_noop(self):
        _, undo = cfg_swap(trakt_refresh_token='')
        try:
            T._trakt_refresh()   # no debe llamar a _session
        finally:
            undo()

    def test_renueva_y_persiste(self):
        _, undo = cfg_swap(trakt_refresh_token='RT', trakt_expires=1,
                           trakt_client_id='c', trakt_client_secret='s',
                           trakt_access_token='OLD')
        s, calls = fake_session({'json': {'access_token': 'NEW',
                                          'refresh_token': 'R2',
                                          'expires_in': 90}})
        try:
            with patch.object(T, '_session', return_value=s), \
                 patch.object(T, '_save_cfg') as save:
                T._trakt_refresh()
            self.assertEqual(T.CFG['trakt_access_token'], 'NEW')
            self.assertTrue(save.called)
            self.assertEqual(calls[0][1], 'https://api.trakt.tv/oauth/token')
        finally:
            undo()


class TestTraktMy(unittest.TestCase):
    def test_sin_token_vacio(self):
        _, undo = cfg_swap(trakt_access_token='')
        try:
            self.assertEqual(T._trakt_my(7), ([], []))
        finally:
            undo()

    def test_eps_y_films(self):
        _, undo = cfg_swap(trakt_access_token='AT', trakt_client_id='c',
                           trakt_refresh_token='RT', trakt_expires=1e18)
        s, _ = fake_session(
            {'json': [{'show': {'ids': {'imdb': 'tt0903747'}, 'title': 'BB', 'year': 2008},
                       'episode': {'season': 1, 'number': 2},
                       'first_aired': '2025-06-01T00:00:00Z'},
                      {'show': {'ids': {}}, 'episode': {}}]},
            {'json': [{'movie': {'ids': {'imdb': 'tt0137523'}, 'title': 'FC',
                                 'year': 1999}, 'released': '2025-07-01'}]})
        try:
            with patch.object(T, '_session', return_value=s):
                eps, films = T._trakt_my(7)
        finally:
            undo()
        self.assertEqual(eps[0][0], 'tt0903747')
        self.assertEqual(eps[0][1:3], (1, 2))
        self.assertTrue(eps[0][4]['_my'])
        self.assertEqual(films[0]['id'], 'tt0137523')


class TestRecom(unittest.TestCase):
    def test_sin_token_metas_vacias(self):
        _, undo = cfg_swap(trakt_access_token='')
        try:
            self.assertEqual(T._recom('movie'), {'metas': []})
        finally:
            undo()

    def test_related_dedup_cap40(self):
        _, undo = cfg_swap(trakt_access_token='AT', trakt_client_id='c',
                           trakt_refresh_token='RT', trakt_expires=1e18)
        hist = [{'movie': {'ids': {'trakt': 'dune-2021'}}}]
        rel = [{'ids': {'imdb': f'tt{i:07d}'}, 'title': f'R{i}', 'year': 2020}
               for i in range(45)]
        s, _ = fake_session({'json': hist}, {'json': rel})
        try:
            with patch.object(T, '_session', return_value=s):
                d = T._recom('movie')
        finally:
            undo()
        self.assertEqual(len(d['metas']), 40)

    def test_history_falla_vacio(self):
        _, undo = cfg_swap(trakt_access_token='AT', trakt_client_id='c',
                           trakt_refresh_token='RT', trakt_expires=1e18)
        s, _ = fake_session(__import__('requests').RequestException('x'))
        try:
            with patch.object(T, '_session', return_value=s):
                self.assertEqual(T._recom('series'), {'metas': []})
        finally:
            undo()


class TestRecomAdd(unittest.TestCase):
    def test_dedup_y_cap(self):
        out, seen = [], set()
        T._recom_add([{'ids': {'imdb': 'tt1'}, 'title': 'A'},
                      {'ids': {'imdb': 'tt1'}, 'title': 'dup'},
                      {'ids': {'imdb': 'tt2'}, 'title': 'B'}], 'movie', seen, out)
        self.assertEqual([m['id'] for m in out], ['tt1', 'tt2'])
