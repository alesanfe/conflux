# pylint: disable=missing-docstring,protected-access,duplicate-code,non-ascii-name
"""Perf e2e: presupuesto de latencia por interfaz sobre servidor HTTP real.

Cada endpoint medido N veces (mín/med/p95). Presupuestos pensados para que
pasen tanto en replay VCR (determinista, rápido) como en live
(CONFLUX_E2E_LIVE=1) donde upstreams reales tardan segundos: los budgets
acotan el overhead del proxy, no la red upstream.
"""
import concurrent.futures as cf
import json
import os
import tempfile
import threading
import time
import unittest
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
    import re as _re
    return _FX.get(_re.sub(r'(?<!\d)(?:19|20)\d{2}(?!\d)', '{Y}', url))


def _vcr_session(*_a, **_k):
    return fake_session({'json': {}})[0]


def _ms(port, path, n=3, timeout=60):
    """N mediciones del endpoint; devuelve (min, med, p95) en ms.
    Un HTTPError (404/501...) es respuesta completa: cuenta su tiempo real."""
    import urllib.error
    import urllib.request
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(
                    f'http://127.0.0.1:{port}{path}', timeout=timeout) as r:
                r.read()
        except urllib.error.HTTPError as e:
            e.read()
            e.close()
        except Exception:                                    # noqa: BLE001
            ts.append(timeout * 1000)
            continue
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return ts[0], ts[len(ts) // 2], ts[int(len(ts) * 0.95) - 1]


@unittest.skipIf(not _LIVE and not _FX,
                 'sin fixture: scripts/record_e2e.py, o live con CONFLUX_E2E_LIVE=1')
class PerfE2E(unittest.TestCase):
    """Presupuestos por interfaz — se miden TODOS los endpoints públicos."""

    @classmethod
    def setUpClass(cls):
        cls._fd, cls._tmp_cfg = tempfile.mkstemp(suffix='.json')
        os.close(cls._fd)
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
        px.build_manifest()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.H)
        cls.port = cls.httpd.server_address[1]
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()
        t0 = time.time()                               # readiness: poll, no sleep fijo
        while time.time() - t0 < 3:
            try:
                urllib.request.urlopen(
                    f'http://127.0.0.1:{cls.port}/health', timeout=1).close()
                break
            except Exception:
                time.sleep(0.01)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.th.join(timeout=5)
        for undo in reversed(cls._undo):
            undo()
        if os.path.exists(cls._tmp_cfg):
            os.unlink(cls._tmp_cfg)

    # -------- endpoints estáticos: deben ser casi inmediatos --------
    def test_manifest_bajo_2s(self):
        _mn, med, _p95 = _ms(self.port, '/manifest.json')
        self.assertLess(med, 2000, f'manifest p50={med:.0f}ms')

    def test_health_bajo_2s(self):
        _mn, med, _p95 = _ms(self.port, '/health')
        self.assertLess(med, 2000, f'health p50={med:.0f}ms')

    def test_landing_bajo_1s(self):
        _mn, med, _p95 = _ms(self.port, '/')
        self.assertLess(med, 1000, f'landing p50={med:.0f}ms')

    def test_configure_bajo_1s(self):
        _mn, med, _p95 = _ms(self.port, '/configure')
        self.assertLess(med, 1000, f'configure p50={med:.0f}ms')

    def test_admin_bajo_1s(self):
        _mn, med, _p95 = _ms(self.port, '/admin')
        self.assertLess(med, 1000, f'admin p50={med:.0f}ms')

    def test_trakt_status_bajo_1s(self):
        _mn, med, _p95 = _ms(self.port, '/auth/trakt/status')
        self.assertLess(med, 1000, f'trakt-status p50={med:.0f}ms')

    def test_options_bajo_500ms(self):
        import urllib.request
        t0 = time.perf_counter()
        req = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/manifest.json', method='OPTIONS')
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        ms = (time.perf_counter() - t0) * 1000
        self.assertLess(ms, 500, f'OPTIONS {ms:.0f}ms')

    def test_404_bajo_500ms(self):
        _mn, med, _p95 = _ms(self.port, '/noexiste')
        self.assertLess(med, 500, f'404 p50={med:.0f}ms')

    def test_perfil_manifest_bajo_2s(self):
        _mn, med, _p95 = _ms(self.port, '/p/x/manifest.json')
        self.assertLess(med, 2000, f'perfil manifest p50={med:.0f}ms')

    # -------- endpoints con fan-out: budget amplio, acotan deadline --------
    def test_catalog_bajo_5s(self):
        _mn, med, _p95 = _ms(self.port,
                             '/catalog/movie/scinemeta_movie_top.json')
        self.assertLess(med, 5000, f'catalog p50={med:.0f}ms')

    def test_meta_bajo_5s(self):
        _mn, med, _p95 = _ms(self.port, '/meta/movie/tt0137523.json')
        self.assertLess(med, 5000, f'meta p50={med:.0f}ms')

    def test_stream_frio_bajo_deadline(self):
        """Stream frío: budget propio (early-done ~2-3s) + margen."""
        _mn, med, _p95 = _ms(self.port,
                             '/stream/movie/tt0137523.json', n=2)
        self.assertLess(med, 8000, f'stream frío p50={med:.0f}ms')

    def test_stream_caliente_bajo_500ms(self):
        _ms(self.port, '/stream/movie/tt0137523.json', n=1)   # calienta caché
        _mn, med, _p95 = _ms(self.port, '/stream/movie/tt0137523.json')
        self.assertLess(med, 500, f'stream caliente p50={med:.0f}ms')

    def test_busqueda_bajo_deadline(self):
        """La búsqueda tiene deadline global: nunca supera ~10s+margen."""
        _mn, med, _p95 = _ms(
            self.port, '/catalog/movie/suniform_movie_search/search=dune.json',
            n=2)
        self.assertLess(med, 12000, f'búsqueda p50={med:.0f}ms')

    def test_subtitles_bajo_5s(self):
        _mn, med, _p95 = _ms(self.port, '/subtitles/movie/tt0137523.json')
        self.assertLess(med, 5000, f'subtitles p50={med:.0f}ms')

    # -------- concurrencia: el servidor no debe serializar --------
    def test_10_manifest_paralelos_no_serializan(self):
        """10 peticiones concurrentes deben costar ~1, no 10×."""
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(10) as ex:
            list(ex.map(lambda i: _ms(self.port, '/manifest.json', n=1),
                        range(10)))
        total = (time.perf_counter() - t0) * 1000
        self.assertLess(total, 5000, f'10 manifest paralelos: {total:.0f}ms')

    def test_5_stream_paralelos_mismo_id(self):
        """Dedup de collect en vuelo: 5 requesters del mismo stream."""
        _ms(self.port, '/stream/movie/tt0137523.json', n=1)   # caché
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(5) as ex:
            list(ex.map(lambda i: _ms(
                self.port, '/stream/movie/tt0137523.json', n=1), range(5)))
        total = (time.perf_counter() - t0) * 1000
        self.assertLess(total, 3000,
                        f'5 stream paralelos (cache): {total:.0f}ms')


if __name__ == '__main__':
    unittest.main()
