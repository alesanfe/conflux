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


class TestCfgUrlB64(unittest.TestCase):
    """_profile con /cfg/<b64>: whitelist + passthrough si roto."""

    def _h(self):
        return srv.H.__new__(srv.H)

    def test_cfg_b64_valida_filtra(self):
        import base64
        import json
        cfg = {'stream_langs': ['es'], 'admin_token': 'x', 'epg_url': 'evil'}
        b = base64.urlsafe_b64encode(json.dumps(cfg).encode()).decode().rstrip('=')
        h = self._h()
        out = h._profile(f'/cfg/{b}/stream/movie/tt.json')
        self.assertEqual(out, '/stream/movie/tt.json')
        self.assertEqual(srv._PROF.cfg, {'stream_langs': ['es']})  # fuera-keys podadas

    def test_cfg_b64_rota_passthrough(self):
        h = self._h()
        p = h._profile('/cfg/no_es_b64_valido!!/stream/x')
        self.assertEqual(p, '/cfg/no_es_b64_valido!!/stream/x')
        self.assertIsNone(srv._PROF.cfg)

    def test_cfg_no_dict_passthrough(self):
        import base64
        b = base64.urlsafe_b64encode(b'[1,2,3]').decode().rstrip('=')
        h = self._h()
        self.assertEqual(h._profile(f'/cfg/{b}/s/x'), f'/cfg/{b}/s/x')
        self.assertIsNone(srv._PROF.cfg)


class TestOptionsYReply(unittest.TestCase):
    """do_OPTIONS CORS + reply gzip/broken-pipe."""

    def _h(self):
        h = srv.H.__new__(srv.H)
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.wfile = MagicMock()
        h.headers = {}
        return h

    def _hdrs(self, h):
        return {c.args[0]: c.args[1] for c in h.send_header.call_args_list}

    def test_options_cors(self):
        h = self._h()
        h.do_OPTIONS()
        h.send_response.assert_called_with(204)
        hdrs = self._hdrs(h)
        self.assertEqual(hdrs['Access-Control-Allow-Origin'], '*')
        self.assertEqual(hdrs['Access-Control-Allow-Private-Network'], 'true')

    def test_reply_grande_comprime(self):
        h = self._h()
        h.headers = {'Accept-Encoding': 'br, gzip'}
        h.reply({'data': 'x' * 3000})
        hdrs = self._hdrs(h)
        self.assertEqual(hdrs['Content-Encoding'], 'gzip')
        import gzip
        body = h.wfile.write.call_args[0][0]
        self.assertEqual(gzip.decompress(body)[:7], b'{"data"')

    def test_reply_pequena_sin_gzip(self):
        h = self._h()
        h.headers = {'Accept-Encoding': 'gzip'}
        h.reply({'ok': 1})
        self.assertNotIn('Content-Encoding', self._hdrs(h))

    def test_reply_pipe_roto_no_propaga(self):
        h = self._h()
        h.wfile.write.side_effect = BrokenPipeError()
        h.reply({'ok': 1})                          # Stremio cortó: silencio


class TestRedirectsEspeciales(unittest.TestCase):
    """/en/ easynews creds y /nzb/ usenet: decode b64 + 404 si inválido."""

    def _h(self, path):
        h = srv.H.__new__(srv.H)
        h.path = path
        h.headers = {}
        h._redir = MagicMock(return_value=None)
        return h

    def test_en_b64_roto_404(self):
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            h = self._h('/en/%%%basura%%%')
            h.do_GET()
            h._redir.assert_called_with(None)
        finally:
            undo()

    def test_en_con_creds_inyecta_auth(self):
        import base64
        link = base64.urlsafe_b64encode(b'https://members.easynews.com/dl/x.mkv').decode().rstrip('=')
        _, undo = cfg_swap(easynews_user='u s', easynews_pass='p')
        try:
            h = self._h('/en/' + link)
            h.do_GET()
            u = h._redir.call_args[0][0]
            self.assertIn('u%20s:p@members.easynews.com', u)
        finally:
            undo()

    def test_en_sin_creds_404(self):
        import base64
        link = base64.urlsafe_b64encode(b'https://x/y').decode().rstrip('=')
        _, undo = cfg_swap(easynews_user='', easynews_pass='')
        try:
            h = self._h('/en/' + link)
            h.do_GET()
            h._redir.assert_called_with(None)       # sin creds no se sirve el link
        finally:
            undo()

    def test_nzb_link_valido_llama_resolver(self):
        import base64
        link = base64.urlsafe_b64encode(b'https://nzb.example/get/1').decode().rstrip('=')
        _, undo = cfg_swap(debrid_key='k')
        try:
            import unittest.mock as um
            h = self._h('/nzb/' + link)
            with um.patch.object(srv, '_db_usenet', return_value='https://tb/redir') as du:
                h.do_GET()
            du.assert_called_once_with('k', 'https://nzb.example/get/1')
            h._redir.assert_called_with('https://tb/redir')
        finally:
            undo()

    def test_nzb_b64_roto_404(self):
        h = self._h('/nzb/%%%mal%%%')
        h.do_GET()
        h._redir.assert_called_with(None)
