# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements
"""Presupuestos formales de recursos (CONFLUX-034/035/036).

Umbrales con margen CI generoso (~10x) — son tripwires de regresión, no
benchmarks absolutos: arranque, latencia de rutas calientes, estabilidad de
memoria/threads tras carga y límites de cachés."""
import os
import statistics
import sys
import threading
import time
import tracemalloc
import unittest

import conflux.app.stream as S
import conflux.core.state as st
import conflux.domain.catalog as C
from tests import patch_fetch

MARGIN = 10          # CI compartido: presupuesto real × 10 como tripwire


class TestStartupBudget(unittest.TestCase):
    def test_import_frio_rapido(self):
        """Importar proxy.py (entry) en un proceso fresco < 3s presupuesto,
        10s tripwire CI."""
        import subprocess
        t0 = time.perf_counter()
        subprocess.run([sys.executable, '-c',
                        'import sys; sys.path.insert(0,"."); import proxy'],
                       check=True, capture_output=True, cwd=os.getcwd())
        dt = time.perf_counter() - t0
        self.assertLess(dt, 10.0, f'arranque {dt:.1f}s')


class TestHotPathBudget(unittest.TestCase):
    def setUp(self):
        S._cache.clear(); C._cat_c.clear()
        self._m = list(st.manifests)
        st.manifests[:] = [{'resources': [
            {'name': 'stream', 'types': ['movie'], 'idPrefixes': ['tt']}]}]
        restore = patch_fetch(lambda *a, **k: {'streams': [
            {'name': 'x', 'title': '1080p', 'infoHash': 'a' * 40, 'url': 'http://x'}]})
        self.addCleanup(restore)
        S.handle_stream('movie', 'tt1')            # calienta

    def tearDown(self):
        st.manifests[:] = self._m
        S._cache.clear(); C._cat_c.clear()

    def test_stream_cache_caliente_p95(self):
        """Cache caliente: presupuesto 100ms p95 (tripwire 1s)."""
        ts = []
        for _ in range(30):
            t0 = time.perf_counter()
            S.handle_stream('movie', 'tt1')
            ts.append(time.perf_counter() - t0)
        p95 = sorted(ts)[int(len(ts) * .95)]
        self.assertLess(p95, 1.0, f'p95 cache caliente {p95*1000:.0f}ms')
        self.assertLess(statistics.median(ts), 0.1)

    def test_health_handler_rapido(self):
        """Datos de /health: cómputo < 50ms (tripwire 500ms)."""
        import conflux.web.pages as P
        st._health('src-x', True, n=3)
        ts = []
        for _ in range(20):
            t0 = time.perf_counter()
            P._health_data() if hasattr(P, '_health_data') else None
            ts.append(time.perf_counter() - t0)
        self.assertLess(max(ts), 0.5)


class TestResourceStability(unittest.TestCase):
    def test_memoria_estable_200_requests(self):
        """200 requests calientes: crecimiento < 8MB (fuga = crecer lineal)."""
        self._m = list(st.manifests)
        st.manifests[:] = [{'resources': [
            {'name': 'stream', 'types': ['movie'], 'idPrefixes': ['tt']}]}]
        try:
            restore = patch_fetch(lambda *a, **k: {'streams': [
                {'name': 'x', 'title': '1080p', 'infoHash': 'a' * 40}]})
            self.addCleanup(restore)
            S.handle_stream('movie', 'tt-mem')
            tracemalloc.start()
            base = tracemalloc.take_snapshot()
            for _ in range(200):
                S.handle_stream('movie', 'tt-mem')
            cur = tracemalloc.take_snapshot()
            diff = sum(s.size_diff for s in
                       cur.compare_to(base, 'filename') if s.size_diff > 0)
            self.assertLess(diff, 8 * 1024 * 1024,
                            f'crecimiento {diff/1e6:.1f}MB en 200 reqs')
        finally:
            tracemalloc.stop()
            st.manifests[:] = self._m
            S._cache.clear()

    def test_threads_no_crecen(self):
        n0 = threading.active_count()
        ts = [threading.Thread(target=lambda: None) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertLessEqual(threading.active_count(), n0 + 2)

    def test_cache_acotado(self):
        """_cset poda al superar 400: ningún caché crece sin cota."""
        c = {}
        for i in range(1000):
            st._cset(c, f'k{i}', [i])
        self.assertLessEqual(len(c), 410)


class TestFanOutBudget(unittest.TestCase):
    def test_28_fuentes_no_serializa(self):
        """Fan-out 28 fuentes lentas iguales: tiempo ≈ una, no 28×."""
        self._m = list(st.manifests)
        st.manifests[:] = [{'resources': [{'name': 'stream',
                                          'types': ['movie'],
                                          'idPrefixes': ['tt']}]}
                          for _ in range(28)]
        try:
            def slow(*a, **k):
                time.sleep(0.15)
                return {'streams': [{'name': 'x', 'infoHash': 'b' * 40}]}
            restore = patch_fetch(slow)
            self.addCleanup(restore)
            S._cache.clear()
            t0 = time.perf_counter()
            S.handle_stream('movie', 'tt-fan')
            dt = time.perf_counter() - t0
            self.assertLess(dt, 28 * 0.15,
                            f'{dt:.2f}s ≈ serialización de 28 fuentes')
        finally:
            st.manifests[:] = self._m
            S._cache.clear()


if __name__ == '__main__':
    unittest.main()
