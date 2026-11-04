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
        s, calls = fake_session({'json': [
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
        self.assertEqual(calls[0][2]['timeout'], 8)               # 8+1@45

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
                 patch.object(T, '_trakt_device_poll'), \
                 patch.object(T.threading, 'Thread') as th:
                d = T._trakt_device_start()
        finally:
            undo()
        self.assertEqual(d['user_code'], 'ABCD')
        self.assertEqual(T._TRAKT_DEV['device_code'], 'dc')
        self.assertEqual(calls[0][0], 'POST')
        self.assertEqual(calls[0][2]['timeout'], 8)               # 8+1@81
        self.assertGreater(T._TRAKT_DEV['deadline'], __import__('time').time())  # Add@84
        th.assert_called_once()
        self.assertTrue(th.call_args[1].get('daemon'))            # daemon=True (True@85)

    def test_device_start_default_expires_600(self):
        """Sin expires_in en la respuesta -> deadline = now+600 (600+1@84:
        el mutante daría 601s)."""
        from freezegun import freeze_time
        import time as _t
        with freeze_time('2025-01-01 12:00:00'):
            _, undo = cfg_swap(trakt_client_id='cid')
            s, _ = fake_session({'json': {
                'device_code': 'dc', 'user_code': 'U',
                'verification_url': 'https://x'}})                 # sin expires_in
            try:
                with patch.object(T, '_session', return_value=s), \
                     patch.object(T, '_trakt_device_poll'), \
                     patch.object(T.threading, 'Thread'):
                    T._trakt_device_start()
            finally:
                undo()
            self.assertEqual(T._TRAKT_DEV['deadline'], _t.time() + 600)

    def test_device_poll_deadline_exacta_no_entra(self):
        """while time.time() < deadline (Lt@94: `<=` entraría un tick más
        y haría un POST extra)."""
        import time as _t
        now = _t.time()
        _, undo = cfg_swap(trakt_client_id='c', trakt_client_secret='s')
        T._TRAKT_DEV.update({'device_code': 'dc', 'interval': 0,
                             'deadline': now})
        s, calls = fake_session({'json': {'access_token': 'AT'}})
        try:
            with patch.object(T, '_session', return_value=s), \
                 patch.object(T.time, 'time', return_value=now), \
                 patch.object(T.time, 'sleep') as sl:
                T._trakt_device_poll()
        finally:
            undo()
        self.assertFalse(calls, 'deadline==now: no debe entrar al poll')
        sl.assert_not_called()

    def test_device_poll_sleep_max_y_default(self):
        """time.sleep(max(3, interval)): con interval ausente -> 5 (5+1@95);
        con interval=2 -> floor 3 (3+1@95)."""
        import time as _t
        now = _t.time()
        for interval_val, esperado in ((None, 5), (2, 3)):
            dev = {'device_code': 'dc', 'deadline': now + 60}
            if interval_val is not None:
                dev['interval'] = interval_val
            T._TRAKT_DEV.update(dev)
            _, undo = cfg_swap(trakt_client_id='c', trakt_client_secret='s')
            s, calls = fake_session({'json': {}})
            try:
                with patch.object(T, '_session', return_value=s), \
                     patch.object(T.time, 'time', side_effect=[now, now + 61]), \
                     patch.object(T.time, 'sleep') as sl:
                    T._trakt_device_poll()
            finally:
                undo()
                T._TRAKT_DEV.clear()
            self.assertEqual(sl.call_args[0][0], esperado,
                             f'interval={interval_val}')
            self.assertEqual(calls[0][2]['timeout'], 8)           # 8+1@100

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
                 patch.object(T, '_save_cfg'), \
                 patch.object(T, 'log_ev') as le:
                T._trakt_device_poll()
            self.assertEqual(T.CFG['trakt_access_token'], 'AT')
            self.assertGreater(T.CFG['trakt_expires'],
                               __import__('time').time() - 60)  # Add@105
            le.assert_called_once_with('trakt_auth', ok=True)    # True@108
            self.assertFalse(T._TRAKT_DEV)
        finally:
            undo()

    def test_device_poll_expires_default(self):
        """Sin expires_in -> CFG['trakt_expires'] = now + 7776000
        (7776000+1@105)."""
        from freezegun import freeze_time
        import time as _t
        with freeze_time('2025-01-01 12:00:00'):
            _, undo = cfg_swap(trakt_client_id='c', trakt_client_secret='s',
                               trakt_access_token='', trakt_expires=0)
            T._TRAKT_DEV.update({'device_code': 'dc', 'interval': 0,
                                 'deadline': _t.time() + 60})
            s, _ = fake_session({'json': {'access_token': 'AT',
                                          'refresh_token': 'RT'}})  # sin expires_in
            try:
                with patch.object(T, '_session', return_value=s), \
                     patch.object(T.time, 'sleep'), \
                     patch.object(T, '_save_cfg'), \
                     patch.object(T, 'log_ev'):
                    T._trakt_device_poll()
                self.assertEqual(T.CFG['trakt_expires'],
                                 _t.time() + 7776000)   # dentro del try: antes de undo
            finally:
                undo()
                T._TRAKT_DEV.clear()

    def test_device_poll_error_loguea_ko(self):
        """Fallo de red en un tick del poll -> log_ev(ok=False) (False@111).
        time() mockeado: un tick y luego deadline superada -> sale."""
        import time as _t
        now = _t.time()
        _, undo = cfg_swap(trakt_client_id='c', trakt_client_secret='s',
                           trakt_access_token='', trakt_refresh_token='')
        T._TRAKT_DEV.update({'device_code': 'dc', 'interval': 0,
                             'deadline': now + 60})
        s, _ = fake_session(__import__('requests').RequestException('x' * 300))
        try:
            with patch.object(T, '_session', return_value=s), \
                 patch.object(T.time, 'time', side_effect=[now, now + 61]), \
                 patch.object(T.time, 'sleep'), \
                 patch.object(T, 'log_ev') as le:
                T._trakt_device_poll()
            le.assert_called_once()
            self.assertEqual(le.call_args[1].get('ok'), False)
            self.assertEqual(len(le.call_args[1].get('err', '')), 100)  # 100+1@111
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
            self.assertEqual(calls[0][2]['timeout'], 8)               # 8+1@137
            self.assertGreater(T.CFG['trakt_expires'], __import__('time').time() - 60)  # Add@141
        finally:
            undo()

    def test_refresh_expires_default(self):
        """Sin expires_in -> now + 7776000 (7776000+1@141)."""
        from freezegun import freeze_time
        import time as _t
        with freeze_time('2025-01-01 12:00:00'):
            _, undo = cfg_swap(trakt_refresh_token='RT', trakt_expires=1,
                               trakt_client_id='c', trakt_client_secret='s',
                               trakt_access_token='OLD')
            s, _ = fake_session({'json': {'access_token': 'NEW'}})  # sin expires_in
            try:
                with patch.object(T, '_session', return_value=s), \
                     patch.object(T, '_save_cfg'):
                    T._trakt_refresh()
                self.assertEqual(T.CFG['trakt_expires'],
                                 _t.time() + 7776000)   # dentro del try: antes de undo
            finally:
                undo()

    def test_no_renueva_si_quedan_mas_10min(self):
        """expires - now > 600 -> no refresh (mata 600+1/Sub/Lt@124:
        la ventana es -600 y la comparación <)."""
        import time as _t
        _, undo = cfg_swap(trakt_refresh_token='RT', trakt_client_id='c',
                           trakt_client_secret='s', trakt_access_token='OLD',
                           trakt_expires=_t.time() + 700)
        s, calls = fake_session({'json': {'access_token': 'NEW'}})
        try:
            with patch.object(T, '_session', return_value=s):
                T._trakt_refresh()
            self.assertEqual(T.CFG['trakt_access_token'], 'OLD')
        finally:
            undo()
        self.assertFalse(calls, '700s restantes: no debe renovar')

    def test_borde_ventana_10min_renueva(self):
        """expires == now+600: `now < exp-600` falso -> SÍ renueva.
        (Lt@124: el mutante <= no renovaría; freezegun fija el reloj.)"""
        from freezegun import freeze_time
        import time as _t
        with freeze_time('2025-01-01 12:00:00'):
            _, undo = cfg_swap(trakt_refresh_token='RT', trakt_client_id='c',
                               trakt_client_secret='s', trakt_access_token='OLD',
                               trakt_expires=_t.time() + 600)
            s, calls = fake_session({'json': {'access_token': 'NEW'}})
            try:
                with patch.object(T, '_session', return_value=s), \
                        patch.object(T, '_save_cfg'):
                    T._trakt_refresh()
            finally:
                undo()
        self.assertTrue(calls, 'justo en el borde debe renovar')

    def test_medio_segundo_sobre_ventana_no_renueva(self):
        """expires == now+600.5: real no renueva; el mutante -601 sí
        (600+1@124, freezegun fija el reloj)."""
        from freezegun import freeze_time
        import time as _t
        with freeze_time('2025-01-01 12:00:00'):
            _, undo = cfg_swap(trakt_refresh_token='RT', trakt_client_id='c',
                               trakt_client_secret='s', trakt_access_token='OLD',
                               trakt_expires=_t.time() + 600.5)
            s, calls = fake_session({'json': {'access_token': 'NEW'}})
            try:
                with patch.object(T, '_session', return_value=s):
                    T._trakt_refresh()
            finally:
                undo()
        self.assertFalse(calls, '600.5s restantes: dentro-0.5s solo con -601')

    def test_renueva_si_quedan_menos_10min(self):
        """expires - now < 600 -> refresh (complementa el borde de la ventana)."""
        import time as _t
        _, undo = cfg_swap(trakt_refresh_token='RT', trakt_client_id='c',
                           trakt_client_secret='s', trakt_access_token='OLD',
                           trakt_expires=_t.time() + 599)
        s, calls = fake_session({'json': {'access_token': 'NEW'}})
        try:
            with patch.object(T, '_session', return_value=s), \
                    patch.object(T, '_save_cfg'):
                T._trakt_refresh()
        finally:
            undo()
        self.assertTrue(calls, '599s restantes: debe renovar')

    def test_refresh_fallo_loguea_ko(self):
        """Error de red en refresh -> log_ev(ok=False), no ok=True (False@144)."""
        import requests as _rq
        _, undo = cfg_swap(trakt_refresh_token='RT', trakt_expires=1,
                           trakt_client_id='c', trakt_client_secret='s',
                           trakt_access_token='OLD')
        s, _ = fake_session(_rq.RequestException('boom' * 60))
        try:
            with patch.object(T, '_session', return_value=s), \
                    patch.object(T, 'log_ev') as le:
                T._trakt_refresh()
        finally:
            undo()
        le.assert_called_once()
        self.assertEqual(le.call_args[0][0], 'trakt_token')
        self.assertEqual(le.call_args[1].get('ok'), False)
        self.assertEqual(len(le.call_args[1].get('err', '')), 100)  # 100+1@144


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
        s, calls = fake_session(
            {'json': [{'show': {'ids': {'imdb': 'tt0903747'}, 'title': 'BB', 'year': 2008},
                       'episode': {'season': 1, 'number': 2},
                       'first_aired': '2025-06-01T00:00:00Z'},
                      {'show': {'ids': {}}, 'episode': {}}]},
            {'json': [{'movie': {'ids': {'imdb': 'tt0137523'}, 'title': 'FC',
                                 'year': 1999},
                       'released': '2025-07-01T12:34:56Z'}]})  # len>10 -> [:10]
        try:
            with patch.object(T, '_session', return_value=s):
                eps, films = T._trakt_my(7)
        finally:
            undo()
        self.assertEqual(eps[0][0], 'tt0903747')
        self.assertEqual(eps[0][1:3], (1, 2))
        self.assertTrue(eps[0][4]['_my'])
        self.assertEqual(films[0]['id'], 'tt0137523')
        self.assertEqual(films[0]['released'], '2025-07-01T00:00:00.000Z')  # Or/[:10]/+@197
        for _m, _u, kw in calls:
            self.assertEqual(kw['timeout'], 8)                    # 8+1@169/190

    def test_my_envia_bearer(self):
        """_trakt_my pide con auth=True -> header Bearer (mutante True@156:
        sin él, Trakt devolvería 401 y eps/films vacíos)."""
        _, undo = cfg_swap(trakt_access_token='AT', trakt_client_id='c',
                           trakt_refresh_token='RT', trakt_expires=1e18)
        s, calls = fake_session({'json': []}, {'json': []})
        try:
            with patch.object(T, '_session', return_value=s):
                T._trakt_my(7)
        finally:
            undo()
        for _m, _u, kw in calls:
            self.assertEqual(kw['headers'].get('Authorization'), 'Bearer AT')


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
        s, calls = fake_session({'json': hist}, {'json': rel})
        try:
            with patch.object(T, '_session', return_value=s):
                d = T._recom('movie')
        finally:
            undo()
        self.assertEqual(len(d['metas']), 40)
        for _m, _u, kw in calls:
            self.assertEqual(kw['timeout'], 8)                    # 8+1@28 (related)

    def test_kind_movies_vs_shows(self):
        """utype 'movie' -> history/movies; 'series' -> history/shows
        (Eq@226)."""
        for utype, kind in (('movie', 'movies'), ('series', 'shows')):
            _, undo = cfg_swap(trakt_access_token='AT', trakt_client_id='c',
                               trakt_refresh_token='RT', trakt_expires=1e18)
            s, calls = fake_session({'json': []})
            try:
                with patch.object(T, '_session', return_value=s):
                    T._recom(utype)
            finally:
                undo()
            self.assertIn(f'/history/{kind}?', calls[0][1])
            # auth=True en el header del history (True@228)
            self.assertEqual(calls[0][2]['headers'].get('Authorization'),
                             'Bearer AT')
            self.assertEqual(calls[0][2]['timeout'], 8)           # 8+1@231

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
