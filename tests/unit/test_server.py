# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.server — handler + ROUTES."""
import threading
import unittest
from unittest.mock import MagicMock

import conflux.web.server as srv
from tests import cfg_swap


class TestProfile(unittest.TestCase):
    def _h(self):
        return srv.H.__new__(srv.H)

    def tearDown(self):
        srv._PROF.name, srv._PROF.cfg = None, None

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

    def test_cfg_url_nntp_numerico(self):
        """BUG: nntp_port/connections eran solo str — int en cfg-URL rechazado."""
        self.assertTrue(srv.H._cfg_url_ok('nntp_port', 563))
        self.assertTrue(srv.H._cfg_url_ok('nntp_connections', 20))
        self.assertFalse(srv.H._cfg_url_ok('nntp_port', 'abc'))
        self.assertFalse(srv.H._cfg_url_ok('nntp_port', 70000))
        self.assertFalse(srv.H._cfg_url_ok('nntp_connections', 0))


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
        self.assertFalse(ok('calendar_months_ahead', 2)) # clave retirada
        self.assertFalse(ok('trakt_access_token', 'x'))  # OAuth retirado


class TestCfgUrlB64(unittest.TestCase):
    """_profile con /cfg/<b64>: whitelist + passthrough si roto."""

    def tearDown(self):
        srv._PROF.name, srv._PROF.cfg = None, None

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
        h.send_response.assert_called_with(200)        # code=200 default (200+1@67)
        self.assertNotIn('Content-Encoding', self._hdrs(h))

    def test_handler_timeout_30(self):
        """H.timeout=30s: cliente que conecta y calla no retiene el hilo
        (30+1@63)."""
        self.assertEqual(srv.H.timeout, 30)

    def test_log_message_fmt_y_ua_cap(self):
        """log_message -> log_ev(msg=fmt%args, ua=ua[:60])
        (Mod@66: el %% -> otro binop rompe; 60+1@66: ua[:61])."""
        h = self._h()
        h.headers = {'User-Agent': 'U' * 100}
        with MagicMock() as _:
            pass
        import unittest.mock as um
        with um.patch.object(srv, 'log_ev') as le:
            srv.H.log_message(h, 'GET %s HTTP/1.1', '/stream/x')
        self.assertEqual(le.call_args[1].get('msg'), 'GET /stream/x HTTP/1.1')
        self.assertEqual(len(le.call_args[1].get('ua')), 60)

    def test_request_queue_size_64(self):
        """Backlog del kernel = 64 (64+1@403)."""
        self.assertEqual(srv._Srv.request_queue_size, 64)

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
            du.assert_called_once_with('https://nzb.example/get/1')
            h._redir.assert_called_with('https://tb/redir')
        finally:
            undo()

    def test_nzb_b64_roto_404(self):
        h = self._h('/nzb/%%%mal%%%')
        h.do_GET()
        h._redir.assert_called_with(None)

    def test_sab_link_valido_llama_addurl(self):
        import base64
        link = base64.urlsafe_b64encode(b'https://nzb.example/get/1').decode().rstrip('=')
        import unittest.mock as um
        h = self._h('/sab/' + link)
        with um.patch.object(srv, '_sab_add',
                             return_value='http://sab.lan:8080') as sa:
            h.do_GET()
        sa.assert_called_once_with('https://nzb.example/get/1')
        h._redir.assert_called_with('http://sab.lan:8080')

    def test_sab_b64_roto_404(self):
        h = self._h('/sab/%%%mal%%%')
        h.do_GET()
        h._redir.assert_called_with(None)


class TestDbProxy(unittest.TestCase):
    """/dp/ túnel debrid: pasa status/headers upstream y chunks; pipe roto ok."""

    def _h(self):
        h = srv.H.__new__(srv.H)
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.wfile = MagicMock()
        h.headers = {}
        return h

    def _resp(self):
        r = MagicMock()
        r.status_code = 206
        r.headers = {'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-9/100',
                     'X-Skip': 'no'}
        r.iter_content.return_value = iter([b'abc', b'def'])
        return r

    def test_pasa_status_headers_y_chunks(self):
        import unittest.mock as um
        h = self._h()
        resp = self._resp()
        with um.patch.object(srv, '_db_stream', return_value=resp):
            h._db_proxy('real-debrid', 'a' * 40, '0')
        h.send_response.assert_called_with(206)
        hdrs = {c.args[0] for c in h.send_header.call_args_list}
        self.assertIn('Content-Range', hdrs)
        self.assertNotIn('X-Skip', hdrs)              # solo headers Content-*
        self.assertEqual(h.wfile.write.call_count, 2)
        resp.iter_content.assert_called_once_with(1 << 16)   # 64KB (16+1@279)

    def test_sin_stream_404(self):
        import unittest.mock as um
        h = self._h()
        h._redir = MagicMock()
        with um.patch.object(srv, '_db_stream', return_value=None):
            h._db_proxy('real-debrid', 'a' * 40, '0')
        h._redir.assert_called_with(None)

    def test_pipe_roto_cierra_respuesta(self):
        import unittest.mock as um
        h = self._h()
        h.wfile.write.side_effect = BrokenPipeError()
        r = self._resp()
        with um.patch.object(srv, '_db_stream', return_value=r):
            h._db_proxy('p', 'a' * 40, '0')            # no propaga
        r.close.assert_called_once()


class TestTimedRoute(unittest.TestCase):
    """_timed_route: dispatch por ROUTES + contador _rtime por segmento."""

    def _h(self):
        h = srv.H.__new__(srv.H)
        h.reply = MagicMock()
        return h

    def test_route_match_llama_handler(self):
        import re
        import unittest.mock as um
        h = self._h()
        handler = MagicMock(return_value={'ok': 1})
        with um.patch.object(srv, 'ROUTES', [(re.compile(r'/x'), handler)]):
            h._timed_route('/x')
        handler.assert_called_once()
        h.reply.assert_called_once_with({'ok': 1})
        self.assertIn('x', srv._rtime)

    def test_sin_route_404(self):
        import unittest.mock as um
        h = self._h()
        with um.patch.object(srv, 'ROUTES', []):
            h._timed_route('/nada/raro')
        h.reply.assert_called_once_with({'err': 'not found'}, 404)


class TestFormYHtml(unittest.TestCase):
    """_form parsea POST urlencoded/multipart-light y _html sirve página."""

    def _h(self, body=b'', ctype='application/x-www-form-urlencoded'):
        h = srv.H.__new__(srv.H)
        h.headers = {'Content-Type': ctype,
                     'Content-Length': str(len(body))}
        h.rfile = type('R', (), {'read': lambda self_, n: body})()
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.wfile = MagicMock()
        return h

    def test_form_urlencoded(self):
        import urllib.parse
        h = self._h(urllib.parse.urlencode({'a': '1', 'b': 'x y'}).encode())
        out = h._form()
        self.assertEqual(out['a'], ['1'])
        self.assertEqual(out['b'], ['x y'])

    def test_form_vacio(self):
        h = self._h(b'')
        self.assertEqual(h._form(), {})

    def test_html_sirve(self):
        h = self._h()
        h._html('<h1>ok</h1>', 200)
        h.send_response.assert_called_with(200)
        hdrs = {c.args[0]: c.args[1] for c in h.send_header.call_args_list}
        self.assertIn('text/html', hdrs['Content-Type'])

    def test_html_xml_default_200(self):
        """_html/_xml sin code -> send_response(200) (200+1@294 y 200+1@303)."""
        h = self._h()
        h._html('<h1>ok</h1>')
        h.send_response.assert_called_with(200)
        h.send_response.reset_mock()
        h._xml('<rss/>')
        h.send_response.assert_called_with(200)

    def test_form_content_length_no_numerico(self):
        """Content-Length: abc -> ValueError -> ln=0 -> form vacío (no crash)."""
        h = srv.H.__new__(srv.H)
        h.headers = {'Content-Length': 'abc'}
        h.rfile = MagicMock()
        h.rfile.read.return_value = b''
        self.assertEqual(h._form(), {})
        h.rfile.read.assert_called_once_with(0)


class TestPagesAuth(unittest.TestCase):
    """_pages: /admin /configure sin auth -> login 401; con auth -> página."""

    def _h(self):
        h = srv.H.__new__(srv.H)
        h.headers = {}
        h._html = MagicMock()
        return h

    def test_admin_sin_auth_login_401(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h()
            self.assertTrue(h._pages('/admin'))
            h._html.assert_called_once()
            self.assertEqual(h._html.call_args[0][1], 401)
        finally:
            undo()

    def test_configure_sin_auth_login_401(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h()
            self.assertTrue(h._pages('/configure'))
            self.assertEqual(h._html.call_args[0][1], 401)
        finally:
            undo()


class TestDoPost(unittest.TestCase):
    """do_POST: /reload /configure exigen auth; desconocido -> 404."""

    def _h(self, path):
        h = srv.H.__new__(srv.H)
        h.path = path
        h.headers = {}
        h.reply = MagicMock()
        h._redir = MagicMock()
        return h

    def test_reload_sin_auth_403(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h('/reload')
            h.do_POST()
            h.reply.assert_called_once_with({'err': 'forbidden'}, 403)
        finally:
            undo()

    def test_configure_sin_auth_403(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h('/configure')
            h.do_POST()
            h.reply.assert_called_once_with({'err': 'forbidden'}, 403)
        finally:
            undo()

    def test_post_desconocido_404(self):
        h = self._h('/wat')
        h.do_POST()
        h.reply.assert_called_once_with({'err': 'not found'}, 404)

    def test_reload_ok_true_y_thread_daemon(self):
        """reply {'ok': True, config} y refresh en daemon=True (True@334/331:
        ok False rompería el cliente; no-daemon el cierre)."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h('/reload')
            h.headers = {'X-Admin-Token': 'secreto'}
            import unittest.mock as um
            with um.patch.object(srv, 'load_config'), \
                    um.patch.object(srv.threading, 'Thread') as th:
                h.do_POST()
            self.assertIs(h.reply.call_args[0][0]['ok'], True)
            self.assertTrue(th.call_args[1].get('daemon'))
        finally:
            undo()

    def test_configure_con_auth_guarda_y_303(self):
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h('/configure')
            h.headers = {'X-Admin-Token': 'secreto'}
            h._form = MagicMock(return_value={'a': ['1']})
            import unittest.mock as um
            with um.patch.object(srv, '_configure_save') as cs:
                h.do_POST()
            cs.assert_called_once_with({'a': ['1']})
            h._redir.assert_called_once_with('/configure', code=303)
        finally:
            undo()


class TestRtimeCap(unittest.TestCase):
    """_rtime: >64 claves distintas -> colapsa a '_other' (crawlers con paths
    arbitrarios no inflan el mapa)."""

    def setUp(self):
        self._rt, self._rts = dict(srv._rtime), {k: v for k, v in srv._rts.items()}
        srv._rtime.clear(); srv._rts.clear()

    def tearDown(self):
        srv._rtime.clear(); srv._rtime.update(self._rt)
        srv._rts.clear(); srv._rts.update(self._rts)

    def test_seg_colapsa_a_other(self):
        h = srv.H.__new__(srv.H)
        h.reply = MagicMock()
        for i in range(64):
            srv._rtime[f's{i}'] = (1, 1.0, 1.0)
        h._timed_route('/nuevo/x')
        self.assertNotIn('nuevo', srv._rtime)
        self.assertIn('_other', srv._rtime)


class TestCfgUrlRota(unittest.TestCase):
    """/cfg/<b64> con charset válido pero JSON roto -> perfil normal (except)."""

    def test_b64_valido_json_roto(self):
        import base64
        seg = base64.urlsafe_b64encode(b'{"roto').decode().rstrip('=')
        h = srv.H.__new__(srv.H)
        out = h._profile(f'/cfg/{seg}/stream/movie/tt.json')
        self.assertEqual(out, f'/cfg/{seg}/stream/movie/tt.json')
        self.assertIsNone(srv._PROF.cfg)


class TestMain(unittest.TestCase):
    """main(): cache->bg vs live, bind_fail -> SystemExit, TLS wrap, SIGTERM."""

    def _fakesrv(self, made, exc=None):
        """Clase servidor falsa: Srv la SUBCLASEA dentro de main()."""
        class FakeSrv:
            daemon_threads = True
            def __init__(self, addr, h):
                if exc:
                    raise exc
                self.socket = MagicMock()
                self.serve_forever = MagicMock()
                made.append(self)
        return FakeSrv

    def _faketh(self, rec=None):
        """threading falso para srv: Thread registra el target; siempre 'main'."""
        rec = rec if rec is not None else []
        return type('T', (), {
            'Thread': staticmethod(
                lambda target=None, daemon=None: rec.append(target) or MagicMock()),
            'Semaphore': threading.Semaphore,
            'current_thread': staticmethod(lambda: 'm'),
            'main_thread': staticmethod(lambda: 'm')}), rec

    def test_main_con_cache_no_fetch_vivo(self):
        import unittest.mock as um
        made, faketh, rec = [], *self._faketh()
        with um.patch.object(srv, '_load_manifests_cache', return_value=[{'x': 1}]), \
             um.patch.object(srv, 'refresh_manifests') as rf, \
             um.patch.object(srv, 'threading', faketh), \
             um.patch.object(srv, '_Srv', self._fakesrv(made)), \
             um.patch.object(srv, 'CFG', {}):
            srv.main()
        rf.assert_not_called()                       # cache -> refresh en bg, no en línea
        self.assertEqual(len(rec), 3)                # refresh + refresher + prefetch
        self.assertTrue(made[0].serve_forever.called)

    def test_main_sin_cache_fetch_vivo(self):
        import unittest.mock as um
        made, faketh, _ = [], *self._faketh()
        with um.patch.object(srv, '_load_manifests_cache', return_value=None), \
             um.patch.object(srv, 'refresh_manifests') as rf, \
             um.patch.object(srv, 'threading', faketh), \
             um.patch.object(srv, '_Srv', self._fakesrv(made)), \
             um.patch.object(srv, 'CFG', {}):
            srv.main()
        rf.assert_called_once()

    def test_main_puerto_ocupado_systemexit(self):
        import unittest.mock as um
        made, faketh, _ = [], *self._faketh()
        with um.patch.object(srv, '_load_manifests_cache', return_value=[{}]), \
             um.patch.object(srv, 'threading', faketh), \
             um.patch.object(srv, '_Srv',
                            self._fakesrv(made, OSError('busy'))):
            with self.assertRaises(SystemExit):
                srv.main()

    def test_main_tls_wrap_socket(self):
        import unittest.mock as um
        made, faketh, _ = [], *self._faketh()
        ctx = MagicMock()
        with um.patch.object(srv, '_load_manifests_cache', return_value=[{}]), \
             um.patch.object(srv, 'threading', faketh), \
             um.patch.object(srv, '_Srv', self._fakesrv(made)), \
             um.patch.object(srv, 'CFG', {'tls_cert': 'c.pem', 'tls_key': 'k.pem'}), \
             um.patch.object(srv.ssl, 'SSLContext', return_value=ctx):
            srv.main()
        ctx.load_cert_chain.assert_called_once_with('c.pem', 'k.pem')
        ctx.wrap_socket.assert_called_once()
        self.assertTrue(ctx.wrap_socket.call_args[1]['server_side'])
        self.assertIs(made[0].socket, ctx.wrap_socket.return_value)

    def test_sigterm_persiste_y_sale(self):
        """El handler _bye registrado en main(): log + _atomic_json + SystemExit."""
        import unittest.mock as um
        made, faketh, _ = [], *self._faketh()
        handlers = {}
        def reg(sig, h):
            handlers[sig] = h
        with um.patch.object(srv, '_load_manifests_cache', return_value=[{}]), \
             um.patch.object(srv, 'threading', faketh), \
             um.patch.object(srv, '_Srv', self._fakesrv(made)), \
             um.patch.object(srv, 'CFG', {}), \
             um.patch.object(srv.signal, 'signal', side_effect=reg), \
             um.patch.object(srv, '_atomic_json') as aj, \
             um.patch.object(srv, '_persist_manifests', return_value={'m': 1}):
            srv.main()
            import signal as _sig
            self.assertIn(_sig.SIGTERM, handlers)
            with self.assertRaises(SystemExit):
                handlers[_sig.SIGTERM]()          # dentro del with: aj aún mockeado
        aj.assert_called_once()


class TestWiringNuevasRutas(unittest.TestCase):
    """do_GET -> /metrics, /torznab/api, /newznab/api (wiring, no lógica)."""

    def _h(self, path):
        h = srv.H.__new__(srv.H)
        h.path = path
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.wfile = unittest.mock.MagicMock()
        h.headers = {}
        return h

    def test_metrics_route(self):
        h = self._h('/metrics')
        h.do_GET()
        h.send_response.assert_called_with(200)
        body = h.wfile.write.call_args[0][0]
        self.assertIn(b'conflux_source_requests_total', body)
        ct = [c[0] for c in h.send_header.call_args_list
              if c[0][0] == 'Content-Type'][0]
        self.assertIn('text/plain', ct[1])

    def test_torznab_route_caps(self):
        _, undo = cfg_swap(torzapi_key='', admin_token='')
        try:
            h = self._h('/torznab/api?t=caps')
            h.do_GET()
        finally:
            undo()
        h.send_response.assert_called_with(200)
        body = h.wfile.write.call_args[0][0]
        self.assertIn(b'<caps>', body)

    def test_newznab_route_alias(self):
        _, undo = cfg_swap(torzapi_key='', admin_token='')
        try:
            h = self._h('/newznab/api?t=caps')
            h.do_GET()
        finally:
            undo()
        h.send_response.assert_called_with(200)
        self.assertIn(b'<caps>', h.wfile.write.call_args[0][0])

    def test_torznab_401_sin_key(self):
        _, undo = cfg_swap(torzapi_key='k9', admin_token='')
        try:
            h = self._h('/torznab/api?t=caps&apikey=malo')
            h.do_GET()
        finally:
            undo()
        h.send_response.assert_called_with(401)


class TestCfgUrlOkBounds(unittest.TestCase):
    """Cotas exactas de _CFG_URL_NUM (LtE@151 x2, 24/100000/30/65535/64+1)."""

    def test_cotas_borde(self):
        ok = srv.H._cfg_url_ok
        for k, lo, hi in [('stream_max_gb', 0, 100000),
                          ('source_timeout', 0, 30),
                          ('nntp_port', 1, 65535),
                          ('nntp_connections', 1, 64)]:
            self.assertTrue(ok(k, lo), f'{k} lo={lo}')
            self.assertTrue(ok(k, hi), f'{k} hi={hi}')
            self.assertFalse(ok(k, lo - 1), f'{k} lo-1')
            self.assertFalse(ok(k, hi + 1), f'{k} hi+1')
        self.assertFalse(ok('nntp_port', True))       # bool != int (type is int)


class TestCfgUrlOkGuards(unittest.TestCase):
    """Guards isinstance (And@141/143/144/146/147: or acepta tipos malos)."""

    def test_list_requiere_lista_str(self):
        ok = srv.H._cfg_url_ok
        self.assertFalse(ok('stream_langs', 'es'))          # str != list
        self.assertFalse(ok('stream_langs', ['es', 5]))
        self.assertTrue(ok('stream_langs', ['es']))

    def test_dict_str_str(self):
        ok = srv.H._cfg_url_ok
        self.assertTrue(ok('debrid_providers', {'rd': 'k'}))
        self.assertFalse(ok('debrid_providers', 'x'))
        self.assertFalse(ok('debrid_providers', {1: 'k'}))
        self.assertFalse(ok('debrid_providers', {'rd': 5}))

    def test_dlist_dict_de_listas(self):
        ok = srv.H._cfg_url_ok
        self.assertTrue(ok('source_groups', {'movie': ['A', 'B']}))
        self.assertFalse(ok('source_groups', {'movie': 'A'}))
        self.assertFalse(ok('source_groups', {'movie': ['A', 5]}))
        self.assertFalse(ok('source_groups', 'x'))


class TestReplyGzipBorde(unittest.TestCase):
    """len(body) > 1024 -> gzip (Gt@70, 1024+1: el borde exacto no comprime)."""

    def _h(self):
        h = srv.H.__new__(srv.H)
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.wfile = MagicMock()
        h.headers = {'Accept-Encoding': 'gzip'}
        return h

    def test_exactamente_1024_no_comprime(self):
        h = self._h()
        h.reply({'x': 'a' * 1015})              # {"x": "..."} = 1024 bytes
        hdrs = {c.args[0] for c in h.send_header.call_args_list}
        self.assertNotIn('Content-Encoding', hdrs)

    def test_1025_comprime(self):
        h = self._h()
        h.reply({'x': 'a' * 1016})
        hdrs = {c.args[0] for c in h.send_header.call_args_list}
        self.assertIn('Content-Encoding', hdrs)


class TestTimedRouteLatencia(unittest.TestCase):
    """ms=(t-t0)*1000 acumulado (Sub/Mult/1000+1@185, Add@189 x2, 512+1@190)."""

    def setUp(self):
        self._rt = dict(srv._rtime)
        self._rts = {k: v for k, v in srv._rts.items()}
        srv._rtime.clear(); srv._rts.clear()

    def tearDown(self):
        srv._rtime.clear(); srv._rtime.update(self._rt)
        srv._rts.clear(); srv._rts.update(self._rts)

    def _h(self):
        h = srv.H.__new__(srv.H)
        h.reply = MagicMock()
        return h

    def test_acumulado_exacto(self):
        import re
        import unittest.mock as um
        h = self._h()
        it = iter([100.0, 101.0])               # t0=100, t1=101 -> ms=1000
        with um.patch.object(srv, 'ROUTES',
                             [(re.compile(r'/seg'), lambda *a: {'ok': 1})]), \
                um.patch.object(srv.time, 'time', side_effect=lambda: next(it)):
            h._timed_route('/seg')
        self.assertEqual(srv._rtime['seg'], (1, 1000.0, 1000.0))

    def test_segundo_hit_acumula(self):
        import re
        import unittest.mock as um
        h = self._h()
        it = iter([100.0, 101.0, 200.0, 203.0])
        with um.patch.object(srv, 'ROUTES',
                             [(re.compile(r'/seg'), lambda *a: {'ok': 1})]), \
                um.patch.object(srv.time, 'time', side_effect=lambda: next(it)):
            h._timed_route('/seg')
            h._timed_route('/seg')
        self.assertEqual(srv._rtime['seg'], (2, 4000.0, 3000.0))

    def test_rts_deque_maxlen_512(self):
        import re
        import unittest.mock as um
        h = self._h()
        it = __import__('itertools').count(0.0)
        with um.patch.object(srv, 'ROUTES',
                             [(re.compile(r'/x'), lambda *a: {'ok': 1})]), \
                um.patch.object(srv.time, 'time', side_effect=lambda: next(it)):
            for _ in range(513):
                h._timed_route('/x')
        self.assertEqual(len(srv._rts['x']), 512)


class TestPagesDispatch(unittest.TestCase):
    """Gate admin solo en /admin|/configure; returns True; landing con Host."""

    def _h(self, headers=None):
        h = srv.H.__new__(srv.H)
        h.headers = headers or {}
        h._html = MagicMock()
        h.reply = MagicMock()
        return h

    def test_manifest_no_gateado_con_token(self):
        """And@195: 'or' pondría /manifest.json tras el login wall."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            h = self._h()
            with unittest.mock.patch.object(srv, '_manifest_reply',
                                            return_value={'m': 1}):
                self.assertTrue(h._pages('/manifest.json'))
            h.reply.assert_called_once()            # manifest servido
            h._html.assert_not_called()             # sin login 401
        finally:
            undo()

    def test_pages_devuelve_true(self):
        """True@202/210: False caería al dispatcher -> 404 posterior."""
        h = self._h()
        self.assertIs(h._pages('/health'), True)
        with unittest.mock.patch.object(srv, '_landing_page',
                                        return_value='<h>'):
            self.assertIs(h._pages('/'), True)

    def test_landing_usa_host_header(self):
        """Host del request manda en la landing (Or@203: and -> 127.0.0.1)."""
        h = self._h({'Host': 'mi.host:1234'})
        with unittest.mock.patch.object(srv, '_landing_page',
                                        return_value='<h>') as lp:
            h._pages('/')
        self.assertEqual(lp.call_args[0][0], 'mi.host:1234')


class TestCfgB64Padding(unittest.TestCase):
    def tearDown(self):
        srv._PROF.name, srv._PROF.cfg = None, None


    """'=' * (-len % 4) de padding y _PROF.name = 'cfg-'+seg[:8]
    (4+1@166, 8+1@168)."""

    def test_padding_correcto_decodifica(self):
        import base64
        import json
        h = srv.H.__new__(srv.H)
        seg = None
        for i in range(60):
            cand = base64.urlsafe_b64encode(json.dumps(
                {'stream_langs': ['es'], 'stream_required': ['x' * i]}
            ).encode()).decode().rstrip('=')
            # len%4==2 (pad '==') y len%5==0 -> mutante -len%5 da pad 0 -> roto
            if len(cand) % 4 == 2 and len(cand) % 5 == 0:
                seg, want = cand, ['x' * i]
                break
        self.assertIsNotNone(seg, 'ningún seg con len%20==10 en 60 pruebas')
        out = h._profile(f'/cfg/{seg}/stream/movie/tt.json')
        self.assertEqual(out, '/stream/movie/tt.json')
        self.assertEqual(srv._PROF.cfg['stream_required'], want)
        self.assertEqual(srv._PROF.name, 'cfg-' + seg[:8])


class TestFormCapYTokenLen(unittest.TestCase):
    """Body 64KB cap (65536+1@346, Or@343) y token [:128] (128+1@352)."""

    def test_form_cap_64k(self):
        h = srv.H.__new__(srv.H)
        h.headers = {'Content-Type': 'application/x-www-form-urlencoded',
                     'Content-Length': '999999'}
        h.rfile = MagicMock()
        h.rfile.read.return_value = b''
        h._form()
        h.rfile.read.assert_called_once_with(65536)

    def test_login_token_trunca_128(self):
        """admin_token de 128 chars + POST de 129 con mismo prefijo:
        [:128] coincide; [:129] rechazaría."""
        import urllib.parse
        _, undo = cfg_swap(admin_token='x' * 128)
        try:
            h = srv.H.__new__(srv.H)
            body = urllib.parse.urlencode(
                {'token': 'x' * 129, 'to': '/admin'}).encode()
            h.headers = {'Content-Type': 'application/x-www-form-urlencoded',
                         'Content-Length': str(len(body))}
            h.rfile = type('R', (), {'read': lambda s, n: body})()
            h.send_response = MagicMock()
            h.send_header = MagicMock()
            h.end_headers = MagicMock()
            h.wfile = MagicMock()
            h._login()
        finally:
            undo()
        hdrs = {c.args[0]: c.args[1] for c in h.send_header.call_args_list}
        self.assertIn('conflux_auth=', hdrs.get('Set-Cookie', ''))


class TestRoutesGroups(unittest.TestCase):
    """m.group(N) correctos por ruta (2+1/3+1@387-395: índices cruzados)."""

    def _dispatch(self, path):
        h = srv.H.__new__(srv.H)
        h.reply = MagicMock()
        h._timed_route(path)
        return h

    def test_stream_args(self):
        import unittest.mock as um
        with um.patch.object(srv, 'handle_stream', return_value={}) as hs:
            self._dispatch('/stream/movie/tt1.json')
        hs.assert_called_once_with('movie', 'tt1')

    def test_catalog_args_extra_y_sin(self):
        import unittest.mock as um
        with um.patch.object(srv, 'handle_catalog', return_value={}) as hc:
            self._dispatch('/catalog/movie/cat.id/extra%20p.json')
        hc.assert_called_once_with('movie', 'cat.id', 'extra%20p')
        with um.patch.object(srv, 'handle_catalog', return_value={}) as hc:
            self._dispatch('/catalog/series/cat.id.json')
        hc.assert_called_once_with('series', 'cat.id', None)

    def test_meta_y_subtitles_args(self):
        import unittest.mock as um
        with um.patch.object(srv, 'handle_meta', return_value={}) as hm:
            self._dispatch('/meta/series/tt2.json')
        hm.assert_called_once_with('series', 'tt2')
        with um.patch.object(srv, 'handle_subtitles', return_value={}) as hs:
            self._dispatch('/subtitles/anime/k.json')
        hs.assert_called_once_with('anime', 'k')


class TestSrvAttrsYDemonios(unittest.TestCase):
    """daemon_threads/request_queue_size/backlog y daemon=True de los threads
    de main (True@401/436/441/442, 64+1@402) — no-daemon bloquearía el cierre."""

    def test_srv_class_attrs(self):
        self.assertTrue(srv._Srv.daemon_threads)
        self.assertEqual(srv._Srv.request_queue_size, 64)

    def test_main_threads_daemon(self):
        import unittest.mock as um
        made, daemons = [], []

        class FakeSrv:
            daemon_threads = True
            def __init__(self, addr, h):
                self.socket = MagicMock()
                self.serve_forever = MagicMock()
                made.append(self)
        faketh = type('T', (), {
            'Thread': staticmethod(
                lambda target=None, daemon=None: (
                    daemons.append(daemon), MagicMock())[1]),
            'Semaphore': threading.Semaphore,
            'current_thread': staticmethod(lambda: 'm'),
            'main_thread': staticmethod(lambda: 'm')})
        with um.patch.object(srv, '_load_manifests_cache', return_value=[{}]), \
                um.patch.object(srv, 'refresh_manifests'), \
                um.patch.object(srv, 'threading', faketh), \
                um.patch.object(srv, '_Srv', FakeSrv), \
                um.patch.object(srv, 'CFG', {}):
            srv.main()
        self.assertEqual(len(daemons), 3)
        self.assertTrue(all(daemons))

    def test_tls_exige_cert_y_key(self):
        """cert sin key -> sin wrap (And@452: or envolvería con key None)."""
        import unittest.mock as um
        made = []

        class FakeSrv:
            daemon_threads = True
            def __init__(self, addr, h):
                self.socket = MagicMock()
                self.serve_forever = MagicMock()
                made.append(self)
        faketh = type('T', (), {
            'Thread': staticmethod(lambda target=None, daemon=None: MagicMock()),
            'Semaphore': threading.Semaphore,
            'current_thread': staticmethod(lambda: 'm'),
            'main_thread': staticmethod(lambda: 'm')})
        ctx = MagicMock()
        with um.patch.object(srv, '_load_manifests_cache', return_value=[{}]), \
                um.patch.object(srv, 'refresh_manifests'), \
                um.patch.object(srv, 'threading', faketh), \
                um.patch.object(srv, '_Srv', FakeSrv), \
                um.patch.object(srv, 'CFG', {'tls_cert': 'c.pem'}), \
                um.patch.object(srv.ssl, 'SSLContext', return_value=ctx):
            srv.main()
        ctx.wrap_socket.assert_not_called()


class TestDobleBind(unittest.TestCase):
    """BUG (Windows): SO_REUSEADDR permite doble bind silencioso sobre el
    mismo puerto -> dos instancias sirven código distinto a la vez (la
    guarda `bind_fail` de main() nunca podía disparar). _Srv debe usar
    SO_EXCLUSIVEADDRUSE en win32 (en POSIX el kernel ya rechaza el
    segundo listen bind). Regresión: instancias stale en :9700."""

    def test_segunda_instancia_rechazada(self):
        h_cls = srv.H
        s1 = srv._Srv(('127.0.0.1', 0), h_cls)
        try:
            port = s1.server_address[1]
            with self.assertRaises(OSError):
                srv._Srv(('127.0.0.1', port), h_cls)
        finally:
            s1.server_close()
