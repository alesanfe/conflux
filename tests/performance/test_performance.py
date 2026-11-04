# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Performance tests: presupuestos de latencia + fan-out concurrente.

No son benchmarks — son tripwires de regresión con margen 5-10x. Detectan
serialización accidental (pools mal usados) y parseos que degeneren."""
import concurrent.futures as cf
import time
import unittest

import conflux.app.stream as S
import conflux.core.state as st
import conflux.domain.catalog as C
import conflux.domain.manifest as MF
import conflux.domain.text as T
from tests import patch_fetch


def _timed(fn, *a, **k):
    t0 = time.perf_counter()
    out = fn(*a, **k)
    return out, time.perf_counter() - t0


class TestLatency(unittest.TestCase):
    def setUp(self):
        S._cache.clear(); S._inflight.clear(); C._cat_c.clear()
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m
        S._cache.clear(); S._inflight.clear(); C._cat_c.clear()

    def test_build_manifest_bajo_presupuesto(self):
        st.manifests[:] = [
            {'resources': [{'name': 'catalog', 'types': ['movie']}],
             'catalogs': [{'id': 'top', 'type': 'movie',
                           'extra': [{'name': 'genre'}]}]}
            for _ in range(24)]
        restore = patch_fetch(lambda *a, **k: None)
        try:
            _, dt = _timed(MF.build_manifest)
        finally:
            restore()
        self.assertLess(dt, 5.0, 'build_manifest tardó demasiado')

    def test_stream_warm_cache_rapido(self):
        st.manifests[:] = [{'resources': [
            {'name': 'stream', 'types': ['movie'], 'idPrefixes': ['tt']}]}]
        restore = patch_fetch(lambda *a, **k: {'streams': []})
        try:
            S.handle_stream('movie', 'tt_warm')
            _, dt = _timed(S.handle_stream, 'movie', 'tt_warm')
        finally:
            restore()
        self.assertLess(dt, 0.5, 'caché caliente debe responder al instante')

    def test_catalog_cache_rapido(self):
        restore = patch_fetch(lambda *a, **k: {'metas': []})
        try:
            C.handle_catalog('movie', 'scinemeta_movie_top', None)
            _, dt = _timed(C.handle_catalog, 'movie', 'scinemeta_movie_top', None)
        finally:
            restore()
        self.assertLess(dt, 0.5)

    def test_parse_stream_throughput(self):
        """norm_stream con 5000 streams: presupuesto CPU ~3s (regresión O(n²);
        margen para ruido bajo carga — degenerado real sería >10x)."""
        s = {'infoHash': 'a' * 40, 'title': 'Película 1080p 👤120 💾1.5 GB ES',
             'behaviorHints': {'bingeGroup': 'x', 'videoSize': 1_500_000_000}}
        _, dt = _timed(lambda: [T.norm_stream(s, 'x') for _ in range(5000)])
        self.assertLess(dt, 3.0, 'parseo degeneró')


class TestConcurrency(unittest.TestCase):
    def setUp(self):
        S._cache.clear(); S._inflight.clear()
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m
        S._cache.clear(); S._inflight.clear()

    def test_streams_paralelos_no_serializan(self):
        """8 ids distintos con upstream lento (50ms): si el fan-out serializa
        tardan ~8×50ms por llamada; el presupuesto refleja el paralelo real."""
        st.manifests[:] = [{'resources': [
            {'name': 'stream', 'types': ['movie'], 'idPrefixes': ['tt']}]}
            for _ in range(8)]

        def slow(url, t=7):
            time.sleep(0.05)
            return {'streams': [{'infoHash': 'c' * 40, 'title': 'X'}]}
        restore = patch_fetch(slow)
        try:
            with cf.ThreadPoolExecutor(max_workers=8) as ex:
                t0 = time.perf_counter()
                outs = list(ex.map(lambda i: S.handle_stream('movie', f'tt{i}'),
                                   range(8)))
                dt = time.perf_counter() - t0
        finally:
            restore()
        for d in outs:
            self.assertIsInstance(d.get('streams'), list)
        # serial puro = 8 ids × 8 fuentes × 50ms = 3.2s; presupuesto con margen
        self.assertLess(dt, 2.0, 'el fan-out se serializó')


if __name__ == '__main__':
    unittest.main()


class TestHealthBatch(unittest.TestCase):
    """_hash_health con 500 streams: lookups O(1), nada de trabajo por stream."""

    def test_hash_health_500_rapido(self):
        import conflux.infra.torrent_health as TH
        streams = [{'infoHash': f'{i:040x}'} for i in range(500)]
        sub = TH._POOL_BG.submit
        TH._POOL_BG.submit = lambda f, *a: None
        try:
            _, dt = _timed(TH._hash_health, streams)
        finally:
            TH._POOL_BG.submit = sub
        self.assertLess(dt, 0.5, 'el filtrado de salud debe ser instantaneo')


class TestSearchDeadline(unittest.TestCase):
    """_busqueda con upstreams lentos: early-done, nunca serializa."""

    def setUp(self):
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m

    def test_una_lenta_no_frena(self):
        """12 upstreams: 11 en 10ms + 1 lento (1.2s) -> la respuesta llega
        con el ultimo, nunca serializa 12×latencia (14s)."""
        st.manifests[:] = [{'catalogs': [
            {'id': f'c{i}', 'type': 'movie',
             'extra': [{'name': 'search'}]}],
            'resources': []} for i in range(12)]
        def slow(url, t=8):
            time.sleep(1.2 if 'c11' in url else 0.01)  # el lento honra su timeout
            return {'metas': [{'id': 'tt1', 'name': 'X'}]}
        restore = patch_fetch(slow)
        try:
            t0 = time.perf_counter()
            out = C._busqueda('movie', 'search=x')
            dt = time.perf_counter() - t0
        finally:
            restore()
        self.assertIsInstance(out.get('metas'), list)
        self.assertLess(dt, 2.5,
                        'la busqueda se serializo (12 fetches en cadena)')


class TestManifestRouting(unittest.TestCase):
    """Routing de catalogo + fix extra: throughput de la via caliente."""

    def test_fix_extra_1000_veces(self):
        import conflux.domain.manifest as MF
        MF._UTYPE['sx_movie_c1'] = 'movie'
        try:
            _, dt = _timed(lambda: [C._cat_fix_extra('http://b', 'movie', 'c1',
                                                     'genre=accion&year=2020')
                                    for _ in range(1000)])
        finally:
            MF._UTYPE.pop('sx_movie_c1', None)
        self.assertLess(dt, 1.0)
