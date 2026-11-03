# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.server — handler + ROUTES."""
import unittest
from unittest.mock import MagicMock

import conflux.web.server as srv
from tests import cfg_swap


class TestProfile(unittest.TestCase):
    def _h(self):
        return srv.H.__new__(srv.H)

    def test_perfil_valido(self):
        h = self._h()
        self.assertEqual(h._profile('/p/micfg/stream/movie/tt.json'),
                         '/stream/movie/tt.json')

    def test_perfil_invalido_no_match(self):
        h = self._h()
        self.assertEqual(h._profile('/p/../etc/passwd'), '/p/../etc/passwd')
        self.assertEqual(h._profile('/stream/x'), '/stream/x')

    def test_perfil_nombre_largo_no_match(self):
        h = self._h()
        self.assertEqual(h._profile('/p/' + 'a' * 40 + '/stream'), '/p/' + 'a' * 40 + '/stream')


class TestRoutes(unittest.TestCase):
    def test_stream_match(self):
        rx, _ = srv.ROUTES[1]
        m = rx.match('/stream/movie/tt0137523.json')
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 'movie')

    def test_catalog_extra_match(self):
        rx, _ = srv.ROUTES[2]
        m = rx.match('/catalog/series/suniversal_series_search/search=dune.json')
        self.assertIsNotNone(m)

    def test_catalog_sin_extra(self):
        rx, _ = srv.ROUTES[3]
        m = rx.match('/catalog/movie/scinemeta_movie_top.json')
        self.assertIsNotNone(m)

    def test_meta_match(self):
        rx, _ = srv.ROUTES[4]
        m = rx.match('/meta/series/tt0944947.json')
        self.assertIsNotNone(m)


class TestRedir(unittest.TestCase):
    def test_redir_302_con_url(self):
        h = srv.H.__new__(srv.H)
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._redir('http://x')
        h.send_response.assert_called_with(302)

    def test_redir_404_sin_url(self):
        h = srv.H.__new__(srv.H)
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h._redir(None)
        h.send_response.assert_called_with(404)


if __name__ == '__main__':
    unittest.main()


class TestAdminTokenGate(unittest.TestCase):
    """admin_token: vacío = abierto; set = X-Admin-Token o cookie conflux_auth.
    ?token= ya NO se acepta — el token no debe aparecer en URL/historial."""

    def _h(self, path='/admin', tok=None, cookie=None):
        h = unittest.mock.Mock(spec=srv.H)
        h.path = path
        h.headers = {}
        if tok:
            h.headers['X-Admin-Token'] = tok
        if cookie:
            h.headers['Cookie'] = cookie
        return h

    def test_vacio_abierto(self):
        _, undo = cfg_swap(admin_token='')
        try:
            self.assertTrue(srv.H._authd(self._h()))
        finally:
            undo()

    def test_sin_token_403(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            self.assertFalse(srv.H._authd(self._h()))
            self.assertFalse(srv.H._authd(self._h('/admin?token=malo')))
        finally:
            undo()

    def test_token_valido(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            self.assertTrue(srv.H._authd(self._h(tok='secreto')))
            self.assertTrue(srv.H._authd(
                self._h(cookie='x=1; conflux_auth=secreto; y=2')))
        finally:
            undo()

    def test_query_token_rechazado(self):
        """Regresión: ?token=secreto en la URL ya no autentica."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            self.assertFalse(srv.H._authd(self._h('/admin?token=secreto')))
        finally:
            undo()

    def test_cookie_mala_rechazada(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            self.assertFalse(srv.H._authd(self._h(cookie='conflux_auth=malo')))
        finally:
            undo()


class TestLogin(unittest.TestCase):
    """POST /login: cookie HttpOnly+SameSite=Strict; el token nunca va en URL."""

    def _h(self, form):
        h = unittest.mock.Mock(spec=srv.H)
        h._form.return_value = form
        return h

    def _hdrs(self, h):
        return {c.args[0]: c.args[1] for c in h.send_header.call_args_list}

    def test_login_ok_cookie(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h({'token': ['secreto'], 'to': ['/configure']})
            srv.H._login(h)
            h.send_response.assert_called_with(302)
            hdrs = self._hdrs(h)
            self.assertEqual(hdrs['Location'], '/configure')
            ck = hdrs['Set-Cookie']
            self.assertIn('conflux_auth=secreto', ck)
            self.assertIn('HttpOnly', ck)
            self.assertIn('SameSite=Strict', ck)
        finally:
            undo()

    def test_login_mal_token_401(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h({'token': ['malo'], 'to': ['/admin']})
            srv.H._login(h)
            h._html.assert_called_once()
            self.assertEqual(h._html.call_args[0][1], 401)
            h.send_response.assert_not_called()
        finally:
            undo()

    def test_login_open_redirect_bloqueado(self):
        """to= solo admite /admin|/configure — nada de redirects externos."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h({'token': ['secreto'], 'to': ['https://evil.example/x']})
            srv.H._login(h)
            self.assertEqual(self._hdrs(h)['Location'], '/admin')
        finally:
            undo()

    def test_login_inyeccion_cookie(self):
        """Token con CRLF/; se sanea -> no casa -> 401, cookie jamás inyectada."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h({'token': ['secreto\r\nX-Evil: 1'], 'to': ['/admin']})
            srv.H._login(h)
            h._html.assert_called_once()
            h.send_response.assert_not_called()
        finally:
            undo()

    def test_login_sin_admin_token_redirige(self):
        """Sin admin_token no hay gate: /login redirige al destino sin cookie."""
        _, undo = cfg_swap(admin_token='')
        try:
            h = self._h({'token': ['cualquiera'], 'to': ['/configure']})
            srv.H._login(h)
            h._redir.assert_called_once_with('/configure')
        finally:
            undo()


class TestCfgUrlKeys(unittest.TestCase):
    """Whitelist de tipos por clave en /cfg/<b64>/."""

    def test_nuevas_claves_tipos(self):
        ok = srv.H._cfg_url_ok
        self.assertTrue(ok('stream_max_gb', 40))
        self.assertTrue(ok('stream_prefetch', False))
        self.assertFalse(ok('stream_max_gb', '40'))      # str no es int
        self.assertFalse(ok('stream_prefetch', 'false')) # str no es bool
        self.assertFalse(ok('stream_prefetch', 0))       # int no es bool
        self.assertFalse(ok('stream_max_gb', 10**9))     # fuera de cota
        self.assertFalse(ok('calendar_months_ahead', 99))
