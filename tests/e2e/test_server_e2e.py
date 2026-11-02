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
from tests import px


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


class ServerE2E(unittest.TestCase):
    """Boot real: manifests cacheados si hay -> refresh bg; si no, fetch vivo."""

    @classmethod
    def setUpClass(cls):
        # POST /configure no toca el config.json real: fichero temporal
        cls._tmp_cfg = '/tmp/e2e-config.json'
        cfg._CFG_FILE = cls._tmp_cfg
        if os.path.exists(cls._tmp_cfg):
            os.unlink(cls._tmp_cfg)
        # manifests: caché o fetch real
        cached = srv._load_manifests_cache()
        if cached:
            st.manifests[:] = cached
        else:
            px.refresh_manifests()
        # la app siempre pide /manifest.json antes: puebla _SYNTH/_GRP/_REQ/_UTYPE
        px.build_manifest()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.H)
        cls.port = cls.httpd.server_address[1]
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.th.join(timeout=5)
        cls.th.join(timeout=3)
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
        code, d = _get(self.port, '/meta/series/tt0944947.json')
        self.assertEqual(code, 200)
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
