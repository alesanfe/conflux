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
        """norm_stream con 5000 streams: presupuesto CPU ~2s (regresión O(n²))."""
        s = {'infoHash': 'a' * 40, 'title': 'Película 1080p 👤120 💾1.5 GB ES',
             'behaviorHints': {'bingeGroup': 'x', 'videoSize': 1_500_000_000}}
        _, dt = _timed(lambda: [T.norm_stream(s, 'x') for _ in range(5000)])
        self.assertLess(dt, 2.0, 'parseo degeneró')


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
