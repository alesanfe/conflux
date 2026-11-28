# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Endurance: estado acotado bajo operación sostenida en el tiempo.

Los dicts globales (rate-limits, caches, inflight) no deben crecer sin límite
ni quedar envenenados tras horas/días simulados de tráfico — los bugs de esta
familia son invisibles en tests puntuales y solo aparecen a lo largo del
tiempo: entradas que nunca envejecen, sprays multi-IP, keys que sobreviven a
un fallo interno. CLOCK.fake() gobierna el avance del reloj compartido."""
import gc
import json
import os
import tempfile
import threading
import time
import tracemalloc
import unittest
import urllib.error
import urllib.request
from collections import deque
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import conflux.app.stream as S
import conflux.core.state as st
import conflux.web.server as srv
from conflux.core import cfg
from tests import FIX, cfg_swap, fake_session, patch_fetch, patch_session, px


class _Base(unittest.TestCase):
    def setUp(self):
        self._fc = st.CLOCK.fake(1_700_000_000.0)
        self._saved = {}
        for d in (srv._AUTH_TRIES, srv._LOGIN_TRIES):
            self._saved[id(d)] = (d, {k: list(v) for k, v in d.items()})
            d.clear()
        self._saved_cache = dict(S._cache)
        self._saved_inflight = dict(S._inflight)
        S._cache.clear()
        S._inflight.clear()

    def tearDown(self):
        st.CLOCK.reset()
        for d, snap in self._saved.values():
            d.clear()
            for k, v in snap.items():
                d[k] = deque(v)
        S._cache.clear()
        S._cache.update(self._saved_cache)
        S._inflight.clear()
        S._inflight.update(self._saved_inflight)

    def _h(self, ip):
        h = srv.H.__new__(srv.H)
        h.headers = {}
        h.client_address = (ip, 0)
        h.reply = MagicMock()
        h._html = MagicMock()
        h._form = lambda: {'token': ['wrong'], 'to': ['/admin']}
        return h


class TestAuthEndurance(_Base):
    def test_spray_24h_bounded(self):
        """24h simuladas de spray multi-IP a _auth_fail + /login: ambos dicts
        quedan acotados — los intentos stale son peso muerto que debe morir y
        el spray fresco tiene cap duro. Y el atacante persistente sigue
        rate-limited al final."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            for hour in range(24):
                for i in range(200):                       # IPs rotando cada hora
                    ip = f'10.{hour}.{i // 256}.{i % 256}'
                    self._h(ip)._auth_fail()
                    self._h(ip)._login()
                self._fc.advance(3600)
                self.assertLessEqual(len(srv._AUTH_TRIES), 513,
                                     f'hora {hour}: _AUTH_TRIES desbordado')
                self.assertLessEqual(len(srv._LOGIN_TRIES), 513,
                                     f'hora {hour}: _LOGIN_TRIES desbordado')
            # el atacante persistente sigue topando el límite tras el spray
            h = self._h('9.9.9.9')
            for _ in range(10):
                self.assertEqual(h._auth_gate(), 'fail')
            self.assertEqual(h._auth_gate(), 'limit')
        finally:
            undo()

    def test_stale_entries_die(self):
        """Entradas cuyo intento más reciente salió de la ventana de 60s no
        deben sobrevivir: un spray de hace 2 min deja el dict (casi) vacío."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            for i in range(300):
                self._h(f'192.168.{i // 256}.{i % 256}')._auth_fail()
                self._h(f'192.168.{i // 256}.{i % 256}')._login()
            self.assertEqual(len(srv._AUTH_TRIES), 300)
            self._fc.advance(120)                          # todo fuera de ventana
            self._h('8.8.8.8')._auth_fail()                # dispara la poda
            self._h('8.8.8.8')._login()
            self.assertLessEqual(len(srv._AUTH_TRIES), 8)
            self.assertLessEqual(len(srv._LOGIN_TRIES), 8)
        finally:
            undo()


class TestCollectEndurance(_Base):
    class _Pool:
        def submit(self, _fn, *a, **k):
            import concurrent.futures as cf
            f = cf.Future()
            f.set_result(('fake', [], 0))
            return f

    def test_repeated_broken_collects_no_leak(self):
        """Collects cuyo sort final explota: _inflight no acumula keys muertas
        y cada state despierta a sus waiters — sin excepción en el ordenar no
        hay forma de envenenar la entrada."""
        with patch.object(S, '_POOL', self._Pool()), \
             patch.object(S, '_scraper_names', return_value=[]):
            for i in range(50):
                key = ('movie', f'tt_broken_{i}', ())
                stt = {'rows': [('mala',)], 'seen': set(), 'contrib': {},
                       'done': threading.Event(), 'lock': threading.Lock()}
                S._inflight[key] = stt
                S._collect(key, stt)
                self.assertTrue(stt['done'].is_set(), f'iter {i}: done sin set')
                self.assertNotIn(key, S._inflight, f'iter {i}: key huérfana')
        self.assertFalse(S._inflight)
        S._cache.clear()

    def test_cache_churn_bounded(self):
        """Churn de keys distintas por cache: el cap de _cset aguanta y una
        key reciente sigue sirviendo respuesta correcta al final."""
        dis = tuple(sorted(S._eff_disabled_for('movie')))   # key real: off+groups
        for i in range(2000):
            st._cset(S._cache, ('movie', f'tt_churn_{i}', dis),
                     [{'name': f's{i}'}])
        self.assertLessEqual(len(S._cache), 4096)
        self.assertEqual(S.handle_stream('movie', 'tt_churn_1999'),
                         {'streams': [{'name': 's1999'}]})
        S._cache.clear()
        S._cache.update(self._saved_cache)


class TestMixedEndurance(_Base):
    def test_stream_plus_auth_sustained(self):
        """Operación mezclada sostenida: sprays de auth + cache hits de /stream
        intercalados durante horas — todo acotado y la respuesta final sigue
        siendo correcta (ni envenenamiento ni deriva)."""
        _, undo = cfg_swap(admin_token='secreto')
        try:
            dis = tuple(sorted(S._eff_disabled_for('movie')))
            for hour in range(12):
                for i in range(300):
                    self._h(f'172.16.{i // 256}.{i % 256}')._auth_fail()
                    self._h(f'172.16.{i // 256}.{i % 256}')._login()
                for i in range(50):
                    st._cset(S._cache, ('movie', f'tt_mix_{hour}_{i}', dis),
                             [{'name': 'x'}])
                self._fc.advance(3600)
            self.assertLessEqual(len(srv._AUTH_TRIES), 513)
            self.assertLessEqual(len(srv._LOGIN_TRIES), 513)
            self.assertFalse(S._inflight)
            # entrada FRESCA (el último advance envejeció las seeds más allá
            # de CACHE_TTL — miss legítimo): el servicio sigue correcto al final
            st._cset(S._cache, ('movie', 'tt_final', dis), [{'name': 'x'}])
            out = S.handle_stream('movie', 'tt_final')
            self.assertEqual(out, {'streams': [{'name': 'x'}]})
        finally:
            undo()


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


@unittest.skipUnless(_FX, 'sin fixture e2e')
class TestHTTPEndurance(unittest.TestCase):
    """Fuga bajo tráfico HTTP REAL: dos ráfagas equivalentes contra el
    servidor levantado en-proceso (VCR en upstreams, conexiones nuevas —
    mismo patrón que scripts/soak.py). Discrimina fuga de objetos de
    arena-alta del allocator: tras la 1ª ráfaga cachés y pools ya están
    dimensionados, así que la 2ª NO debe retener casi nada. Si los
    objetos vivos (gc) o la memoria traced (tracemalloc) crecen entre
    reposos equivalentes, hay retención real por request."""

    _ROUTES = ('/manifest.json', '/health', '/health/live',
               '/catalog/movie/scinemeta_movie_top.json',
               '/meta/movie/tt0137523.json', '/metrics',
               '/stream/movie/tt0137523.json',
               '/stream/series/tt0944947%3A1%3A1.json')

    @classmethod
    def setUpClass(cls):
        cls._fd, cls._tmp_cfg = tempfile.mkstemp(suffix='.json')
        os.close(cls._fd)
        cls._cfg_file_prev = cfg._CFG_FILE
        cfg._CFG_FILE = cls._tmp_cfg
        if os.path.exists(cls._tmp_cfg):
            os.unlink(cls._tmp_cfg)
        srv.CFG['admin_token'] = ''
        cls._undo = [patch_fetch(_vcr), patch_session(_vcr_session)]
        px.refresh_manifests()
        px.build_manifest()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), srv.H)
        cls.port = cls.httpd.server_address[1]
        cls.th = threading.Thread(target=cls.httpd.serve_forever,
                                  daemon=True)
        cls.th.start()
        t0 = time.time()
        while time.time() - t0 < 3:                        # _wait_ready
            try:
                urllib.request.urlopen(
                    f'http://127.0.0.1:{cls.port}/health',
                    timeout=1).close()
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
        cfg._CFG_FILE = cls._cfg_file_prev
        if os.path.exists(cls._tmp_cfg):
            os.unlink(cls._tmp_cfg)

    def _burst(self, reqs_per_thread=60, workers=4):
        errs = []
        def _w(n):
            for i in range(reqs_per_thread):
                path = self._ROUTES[(n + i) % len(self._ROUTES)]
                try:
                    with urllib.request.urlopen(
                            f'http://127.0.0.1:{self.port}{path}',
                            timeout=20) as r:
                        r.read()
                        if r.status >= 500:
                            errs.append((path, r.status))
                except Exception as e:                     # noqa: BLE001
                    errs.append((path, repr(e)))
        ts = [threading.Thread(target=_w, args=(w,))
              for w in range(workers)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=60)
        return errs

    def _vital(self):
        gc.collect()
        return (len(gc.get_objects()),
                sum(s.size for s in tracemalloc.take_snapshot()
                    .statistics('filename')),
                len(threading.enumerate()))

    def test_dos_rafagas_sin_fuga_objetos(self):
        errs0 = self._burst()                              # calienta caches/pools
        tracemalloc.start()
        try:
            time.sleep(1)
            obj1, mem1, thr1 = self._vital()
            errs1 = self._burst()
            time.sleep(1)
            obj2, mem2, thr2 = self._vital()
            self.assertFalse(errs0 or errs1,
                             f'requests fallidos: {(errs0 + errs1)[:3]}')
            # la 2ª ráfaga sirve las mismas rutas (caches calientes):
            # retención >2% de objetos o >4MB traced = fuga por request
            self.assertLessEqual(obj2, obj1 * 1.02 + 500,
                                 f'objetos vivos {obj1} -> {obj2}')
            self.assertLessEqual(mem2, mem1 + 4 * 1024 * 1024,
                                 f'traced {mem1 >> 20}MB -> {mem2 >> 20}MB')
            self.assertLessEqual(thr2, thr1 + 8,
                                 f'threads {thr1} -> {thr2}')
        finally:
            tracemalloc.stop()
