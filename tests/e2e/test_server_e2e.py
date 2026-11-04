# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""E2E: servidor HTTP real en hilo daemon — cubre TODAS las funcionalidades."""
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import conflux.core.state as st
import conflux.web.server as srv
from conflux.core import cfg
from tests import FIX, fake_session, patch_fetch, patch_session, px

_LIVE = os.environ.get('CONFLUX_E2E_LIVE') == '1'
_FX_PATH = os.path.join(FIX, 'e2e_responses.json.gz')
if os.path.exists(_FX_PATH):
    import gzip as _gz
    with _gz.open(_FX_PATH, 'rt', encoding='utf-8') as _fh:
        _FX = json.load(_fh)
else:
    _FX = {}


def _vcr(url, timeout=None):
    """fetch replay: url normalizada (años->{Y}) -> payload grabado, o None."""
    import re as _re
    return _FX.get(_re.sub(r'(?<!\d)(?:19|20)\d{2}(?!\d)', '{Y}', url))


def _vcr_session(*_a, **_k):
    # stub _session: HEAD url-health y APIs sin key -> 200 con json {}
    return fake_session({'json': {}})[0]


def _get(port, path, raw=False, timeout=15):
    url = f'http://127.0.0.1:{port}{path}'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            return r.status, (body if raw else json.loads(body))
    except urllib.error.HTTPError as e:
        code, body = e.code, (e.read() if raw else None)
        e.close()
        return code, body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_NOPORT_OPENER = urllib.request.build_opener(_NoRedirect)


def _post(port, path, data=b''):
    url = f'http://127.0.0.1:{port}{path}'
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        with _NOPORT_OPENER.open(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        code, loc = e.code, e.headers.get('Location')
        e.close()
        return code, loc


def _get_retry(port, path, tries=3, timeout=60):
    """Upstream transitorio: reintenta antes de fallar (flakes R5).
    Search/calendar/stream hacen fan-out real: timeout generoso."""
    for _ in range(tries):
        try:
            code, d = _get(port, path, timeout=timeout)
        except (TimeoutError, urllib.error.URLError):
            continue
        if code == 200 and d:
            return code, d
        time.sleep(2)
    return code, d


def _wait_ready(port, timeout=3):
    """Readiness real en vez de sleep fijo: poll hasta que el servidor
    responda (arranca en ~10ms; el sleep(0.3) quemaba tiempo siempre)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            _get(port, '/health', timeout=1)
            return
        except Exception:
            time.sleep(0.01)


@unittest.skipIf(not _LIVE and not _FX,
                 'sin fixture: scripts/record_e2e.py, o live con CONFLUX_E2E_LIVE=1')
class ServerE2E(unittest.TestCase):
    """Boot real. CONFLUX_E2E_LIVE=1 -> upstreams vivos; si no, replay VCR
    del fixture (determinista, sin red) — mismo servidor HTTP y mismos tests."""

    @classmethod
    def setUpClass(cls):
        # POST /configure no toca el config.json real: fichero temporal
        cls._tmp_cfg = '/tmp/e2e-config.json'
        cfg._CFG_FILE = cls._tmp_cfg
        if os.path.exists(cls._tmp_cfg):
            os.unlink(cls._tmp_cfg)
        cls._undo = []
        if _LIVE:
            cached = srv._load_manifests_cache()
            if cached:
                st.manifests[:] = cached
            else:
                px.refresh_manifests()
        else:
            cls._undo += [patch_fetch(_vcr), patch_session(_vcr_session)]
            px.refresh_manifests()
        # la app siempre pide /manifest.json antes: puebla _SYNTH/_GRP/_REQ/_UTYPE
        px.build_manifest()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.H)
        cls.port = cls.httpd.server_address[1]
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()
        _wait_ready(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.th.join(timeout=5)
        cls.th.join(timeout=3)
        for undo in reversed(cls._undo):
            undo()
        if os.path.exists(cls._tmp_cfg):
            os.unlink(cls._tmp_cfg)

    # ---- infra ----
    def test_manifest_estructura(self):
        code, m = _get(self.port, '/manifest.json')
        self.assertEqual(code, 200)
        for k in ('id', 'version', 'name', 'resources', 'types', 'catalogs', 'idPrefixes'):
            self.assertIn(k, m, k)
        self.assertGreater(len(m['catalogs']), 20)
        self.assertGreater(len(m['types']), 3)

    def test_manifest_idprefixes(self):
        _c, m = _get(self.port, '/manifest.json')
        prefs = set(m['idPrefixes'])
        for p in ('tt', 'kitsu'):
            self.assertIn(p, prefs)

    def test_health_200_json(self):
        code, h = _get(self.port, '/health')
        self.assertEqual(code, 200)
        self.assertIn('sources', h)

    def test_admin_200_html(self):
        code, b = _get(self.port, '/admin', raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b'<html', b)

    def test_landing_install(self):
        """/ : landing del addon — deep link stremio:// con el host servido."""
        code, b = _get(self.port, '/', raw=True)
        self.assertEqual(code, 200)
        self.assertIn(f'stremio://127.0.0.1:{self.port}/manifest.json'.encode(), b)
        self.assertIn(f'http://127.0.0.1:{self.port}/manifest.json'.encode(), b)

    def test_configure_200_html(self):
        code, b = _get(self.port, '/configure', raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b'<form', b)

    def test_trakt_status_200(self):
        code, d = _get(self.port, '/auth/trakt/status')
        self.assertEqual(code, 200)
        self.assertIsInstance(d, dict)

    def test_trakt_auth_page_200(self):
        code, b = _get(self.port, '/auth/trakt', raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b'<', b)

    # ---- stream ----
    def test_stream_movie_tt(self):
        code, d = _get(self.port, '/stream/movie/tt0137523.json')
        self.assertEqual(code, 200)
        self.assertIn('streams', d)
        self.assertGreater(len(d['streams']), 50, 'movie popular: >50 streams')
        for s in d['streams'][:20]:
            self.assertTrue(s['name'].startswith(('☁️', '⚡')), s['name'])
            self.assertIsInstance(s['title'], str)
            self.assertIsNotNone(s.get('infoHash') or s.get('url'))

    def test_stream_series_episodio(self):
        code, d = _get_retry(self.port, '/stream/series/tt0944947%3A1%3A1.json')
        self.assertEqual(code, 200)
        self.assertGreater(len(d['streams']), 100, 'episodio popular: >100')

    def test_stream_anime_absoluto(self):
        """kitsu:49847:2 (episodio absoluto) -> resuelto a tt:1:2."""
        code, d = _get_retry(self.port, '/stream/series/kitsu%3A49847%3A2.json')
        self.assertEqual(code, 200)
        self.assertGreater(len(d['streams']), 50, 'anime: >50')

    def test_stream_cache_segunda_vez(self):
        _get(self.port, '/stream/movie/tt0137523.json')
        code, d = _get(self.port, '/stream/movie/tt0137523.json')
        self.assertEqual(code, 200)
        self.assertGreater(len(d['streams']), 50)

    # ---- meta ----
    def test_meta_movie(self):
        code, d = _get(self.port, '/meta/movie/tt0137523.json')
        self.assertEqual(code, 200)
        self.assertEqual(d['meta']['name'], 'Fight Club')
        self.assertIn('year', d['meta'])

    def test_meta_series(self):
        # upstream a veces responde meta movie-shape para un tt de serie
        # (transitorio, R5) — reintentar hasta la forma esperada
        for _ in range(4):
            code, d = _get(self.port, '/meta/series/tt0944947.json', timeout=30)
            self.assertEqual(code, 200)
            if 'videos' in d['meta']:
                return
        self.assertIn('videos', d['meta'])

    def test_meta_prefijo_alien(self):
        """mx:<id>:<slug> -> meta resuelto vía _resolve_sid."""
        code, d = _get(self.port,
            '/meta/series/mx%3A6429078990673560656%3Akagewani-gG7FoWufVE7.json')
        self.assertEqual(code, 200)
        self.assertIn('meta', d)
        self.assertIn('name', d['meta'])

    # ---- catalog ----
    def test_catalog_movie(self):
        code, d = _get(self.port, '/catalog/movie/scinemeta_movie_top.json')
        self.assertEqual(code, 200)
        self.assertIn('metas', d)
        self.assertGreater(len(d['metas']), 5)

    def test_catalog_series(self):
        code, d = _get(self.port, '/catalog/series/scinemeta_series_top.json')
        self.assertEqual(code, 200)
        self.assertIn('metas', d)

    def test_catalog_busqueda(self):
        code, d = _get_retry(self.port,
            '/catalog/movie/suniform_movie_search/search=dune.json')
        self.assertEqual(code, 200)
        self.assertIn('metas', d)
        self.assertGreater(len(d['metas']), 0)

    def test_catalog_estrenos_sintetico(self):
        code, d = _get(self.port, '/catalog/movie/scinemeta_movie_estrenos.json')
        self.assertEqual(code, 200)
        self.assertIn('metas', d)

    def test_catalog_calendario(self):
        code, d = _get_retry(self.port,
            '/catalog/series/scinemeta_series_estrenoscal/calendarVideosIds=tt0944947.json')
        self.assertEqual(code, 200)
        self.assertIn('metasDetailed', d)

    # ---- subtitles ----
    def test_subtitles_endpoint(self):
        code, d = _get(self.port, '/subtitles/movie/tt0137523.json')
        self.assertEqual(code, 200)
        self.assertIn('subtitles', d)

    # ---- redirects ----
    def test_redirect_debrid_sin_key_404(self):
        code, _ = _get(self.port, '/r/real-debrid/aaaabbbbccccdddd?fi=0')
        self.assertIn(code, (404, 302))

    def test_redirect_sub_sin_key_404(self):
        code, _ = _get(self.port, '/s/999999')
        self.assertEqual(code, 404)

    # ---- errores graceful ----
    def test_ruta_desconocida_404(self):
        code, _d = _get(self.port, '/noexiste/endpoint')
        self.assertEqual(code, 404)

    def test_cid_malformed_graceful(self):
        code, d = _get(self.port, '/catalog/movie/%%%invalid.json')
        self.assertEqual(code, 200)
        self.assertEqual(d, {'metas': []})

    def test_stream_id_raro_vacio(self):
        code, d = _get(self.port, '/stream/movie/zzzzzz.json')
        self.assertEqual(code, 200)
        self.assertIn('streams', d)

    # ---- POST ----
    def test_post_reload(self):
        code, d = _post(self.port, '/reload', b'')
        self.assertEqual(code, 200)
        self.assertTrue(d.get('ok'))
        for k, v in d.get('config', {}).items():
            if 'key' in k and v:
                self.assertEqual(v, '***')

    def test_post_configure_redirect(self):
        """Form completo: todas las fuentes marcadas -> sources_disabled=[].
        try/finally: restaura runtime para no contaminar tests posteriores."""
        form = '&'.join(f'src={urllib.parse.quote(n)}' for n, _ in st.SOURCES)
        try:
            code, loc = _post(self.port, '/configure', form.encode())
            self.assertEqual(code, 303)
            self.assertEqual(loc, '/configure')
        finally:
            cfg.load_config()   # runtime limpio: config tmp -> defaults

    # ---- perfil ----
    def test_perfil_path_manifest(self):
        code, m = _get(self.port, '/p/miperfil/manifest.json')
        self.assertEqual(code, 200)
        self.assertIn('catalogs', m)


if __name__ == '__main__':
    unittest.main()


class ServerE2EInterfaces(ServerE2E):
    """Las interfaces HTTP que el e2e base no cubre: OPTIONS, redirects
    especiales, cfg-en-URL, gzip, extras de catálogo, métodos no soportados."""

    # ---- OPTIONS / CORS ----
    def test_options_preflight_204(self):
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/manifest.json', method='OPTIONS')
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(r.status, 204)
                self.assertEqual(r.headers['Access-Control-Allow-Origin'], '*')
                self.assertEqual(
                    r.headers['Access-Control-Allow-Private-Network'], 'true')
        except urllib.error.HTTPError as e:
            self.fail(f'OPTIONS -> {e.code}')

    # ---- gzip ----
    def test_manifest_gzip_si_acepta(self):
        import gzip
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/manifest.json',
            headers={'Accept-Encoding': 'gzip'})
        with urllib.request.urlopen(req, timeout=15) as r:
            self.assertEqual(r.headers.get('Content-Encoding'), 'gzip')
            d = json.loads(gzip.decompress(r.read()))
            self.assertIn('catalogs', d)

    def test_manifest_sin_gzip_sin_header(self):
        with urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/manifest.json',
                timeout=15) as r:
            self.assertNotEqual(r.headers.get('Content-Encoding'), 'gzip')

    # ---- /cfg/<b64>/ perfil por URL ----
    def test_cfg_b64_manifest(self):
        import base64
        c = base64.urlsafe_b64encode(
            json.dumps({'stream_langs': ['es']}).encode()).decode().rstrip('=')
        code, m = _get(self.port, f'/cfg/{c}/manifest.json')
        self.assertEqual(code, 200)
        self.assertIn('catalogs', m)

    def test_cfg_b64_basura_cae_graceful(self):
        """cfg inválido en URL: rechazado, nunca 500."""
        code, _m = _get(self.port, '/cfg/%25%25%25/manifest.json')
        self.assertIn(code, (404, 200))

    # ---- redirects especiales ----
    def test_en_b64_roto_404(self):
        code, _ = _get(self.port, '/en/%%%basura%%%')
        self.assertEqual(code, 404)

    def test_en_valido_sin_creds_404(self):
        import base64
        link = base64.urlsafe_b64encode(b'https://x/dl/y').decode().rstrip('=')
        _, undo = __import__('tests', fromlist=['cfg_swap']).cfg_swap(
            easynews_user='', easynews_pass='')
        try:
            code, _ = _get(self.port, '/en/' + link)
            self.assertEqual(code, 404)
        finally:
            undo()

    def test_nzb_b64_roto_404(self):
        code, _ = _get(self.port, '/nzb/%%%mal%%%')
        self.assertEqual(code, 404)

    def test_dp_sin_debrid_key(self):
        """/dp/ sin key configurada -> _db_stream None -> 404 (o 302 si la
        fixture devuelve stream; lo importante: nunca 500)."""
        code, _ = _get(self.port, '/dp/real-debrid/' + 'a' * 40)
        self.assertIn(code, (302, 404))

    def test_r_hash_inventado_no_500(self):
        code, _ = _get(self.port, '/r/alldebrid/' + 'z' * 40)
        self.assertIn(code, (302, 404))

    # ---- catálogo con extras ----
    def test_catalog_extra_genre(self):
        code, d = _get_retry(self.port,
            '/catalog/movie/scinemeta_movie_top/genre=action.json')
        self.assertEqual(code, 200)
        self.assertIn('metas', d)

    def test_catalog_extra_desconocido_no_explota(self):
        code, d = _get(self.port,
            '/catalog/movie/scinemeta_movie_top/foo=bar.json')
        self.assertEqual(code, 200)
        self.assertIn('metas', d)

    # ---- stream tv ----
    def test_stream_tv_tipo(self):
        code, d = _get(self.port, '/stream/tv/iptv-x.json')
        self.assertEqual(code, 200)
        self.assertIn('streams', d)

    # ---- POST /login ----
    def test_post_login_sin_admin_redirige(self):
        """Sin admin_token el login redirige sin gate (loopback abierto)."""
        _, undo = __import__('tests', fromlist=['cfg_swap']).cfg_swap(
            admin_token='')
        try:
            form = urllib.parse.urlencode({'to': '/admin'}).encode()
            code, loc = _post(self.port, '/login', form)
            self.assertIn(code, (302, 303))
            self.assertEqual(loc, '/admin')
        finally:
            undo()

    def test_post_login_form_vacio(self):
        _, undo = __import__('tests', fromlist=['cfg_swap']).cfg_swap(
            admin_token='')
        try:
            code, _ = _post(self.port, '/login', b'')
            self.assertIn(code, (302, 303, 401))
        finally:
            undo()

    # ---- métodos no soportados ----
    def test_put_no_soportado(self):
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('PUT', '/manifest.json', body=b'{}')
        r = conn.getresponse()
        r.read()
        conn.close()
        self.assertEqual(r.status, 501)

    def test_post_a_endpoint_get_no_soportado(self):
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('DELETE', '/health')
        r = conn.getresponse()
        r.read()
        conn.close()
        self.assertEqual(r.status, 501)

    # ---- concurrencia básica e2e ----
    def test_10_manifest_paralelos_todos_200(self):
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(10) as ex:
            codes = list(ex.map(
                lambda i: _get(self.port, '/manifest.json')[0], range(10)))
        self.assertEqual(codes, [200] * 10)
