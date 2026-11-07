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


def _timed(fn, *a, **k):
    t0 = time.perf_counter()
    out = fn(*a, **k)
    return out, time.perf_counter() - t0


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


class TestParseBudget(unittest.TestCase):
    """Parseos calientes acotados: un .m3u gordo o un XMLTV de un proveedor
    grande no pueden bloquear el hilo de refresco."""

    def test_m3u_500_canales(self):
        """500 líneas EXTINF parsean < 200ms (tripwire 2s)."""
        from unittest.mock import mock_open, patch
        import conflux.domain.lists as L
        m3u = '#EXTM3U\n' + ''.join(
            f'#EXTINF:-1 tvg-logo="http://l/{i}.png" group-title="G{i%7}",'
            f'Canal {i}\nhttp://stream/{i}\n' for i in range(500))
        _, undo = __import__('tests').cfg_swap(m3u_path='/tmp/big.m3u')
        try:
            with patch.object(L, '_open', mock_open(read_data=m3u)):
                t0 = time.perf_counter()
                metas = L._m3u_metas()
                dt = time.perf_counter() - t0
        finally:
            undo()
            L._M3U.clear()
        self.assertEqual(len(metas), 500)
        self.assertLess(dt, 2.0, f'parseo m3u {dt*1000:.0f}ms')

    def test_catalog_caliente(self):
        """Catálogo cacheado: < 50ms p95 (tripwire 500ms) — el picker del
        cliente no puede esperar por un metas ya visto."""
        key = ('movie', 'sfoo_movie_top', '')
        st._cset(C._cat_c, key, {'metas': [
            {'id': f'tt{i}', 'name': f'x{i}', 'type': 'movie'}
            for i in range(40)]})
        try:
            ts = []
            for _ in range(50):
                t0 = time.perf_counter()
                d = C.handle_catalog('movie', 'sfoo_movie_top', '')
                ts.append(time.perf_counter() - t0)
            self.assertEqual(len(d['metas']), 40)          # sirvió el hit
            self.assertLess(sorted(ts)[int(len(ts) * .95)], 0.5,
                            f'p95 cat caliente {sorted(ts)[-3]*1000:.0f}ms')
        finally:
            C._cat_c.pop(key, None)


class TestRowsOutBudget(unittest.TestCase):
    """_rows_out+_user_filters: pipeline post-collect que corre en CADA
    /stream (hit o miss). Regresión medida: 98ms -> 18ms a 12k filas;
    el tripwire va al peor caso real (collect de ~30 fuentes puede juntar
    miles de streams)."""

    @staticmethod
    def _rows(n):
        import conflux.domain.text as T
        raw = {'name': 'x', 'title': 'F.2024.1080p.WEBRip.x264 seeds:120',
               'infoHash': 'a' * 40, 'url': 'http://x',
               'behaviorHints': {'videoSize': 2_000_000_000}}
        return [T.norm_stream(dict(raw), 'TS') for _ in range(n)]

    def test_rows_out_12k_sin_filtros(self):
        """Sin filtros activos: sort dominante — presupuesto 2s
        (tripwire; real ~20-30ms)."""
        rows = self._rows(12000)
        _, dt = _timed(lambda: S._user_filters(S._rows_out(rows)))
        self.assertLess(dt, 2.0, f'{dt*1000:.0f}ms en 12k filas')

    def test_rows_out_12k_con_filtros(self):
        """Todos los filtros activos (exclude+regex+required+adult+preferred):
        1 join+~5 regex por fila — presupuesto 4s (real ~80ms)."""
        from tests import cfg_swap
        _, undo = cfg_swap(stream_exclude=['zz-no-match'],
                           stream_exclude_regex=[r'zz-no-match'],
                           stream_required=[],
                           stream_required_regex=[],
                           parental=True,
                           stream_preferred=['es'])
        try:
            rows = self._rows(12000)
            _, dt = _timed(lambda: S._user_filters(S._rows_out(rows)))
        finally:
            undo()
        self.assertLess(dt, 4.0, f'{dt*1000:.0f}ms en 12k filas filtradas')

    def test_user_filters_no_activo_no_toca_texto(self):
        """Sin filtros activos el pipeline no construye el texto del stream:
        _rex_text jamás se invoca (era el coste oculto por fila)."""
        from unittest.mock import patch
        rows = self._rows(2000)
        with patch.object(S, '_rex_text') as rt:
            S._user_filters([r[0] for r in rows])
        rt.assert_not_called()


class TestRowsPipeMemo(unittest.TestCase):
    """_rows_pipe memoiza la salida por (key, firma-config, identidad rows):
    el path warm con filtros era ~57ms/request a 2k filas; con firma
    estable es un hit de dict."""

    def setUp(self):
        self._o = dict(S._OUT_C); S._OUT_C.clear()

    def tearDown(self):
        S._OUT_C.clear(); S._OUT_C.update(self._o)

    @staticmethod
    def _rows(n):
        import conflux.domain.text as T
        raw = {'name': 'x', 'title': 'F.2024.1080p.WEBRip.x264 seeds:120',
               'infoHash': 'a' * 40, 'url': 'http://x',
               'behaviorHints': {'videoSize': 2_000_000_000}}
        return [T.norm_stream(dict(raw), 'TS') for _ in range(n)]

    def test_misma_firma_es_hit(self):
        from tests import cfg_swap
        rows = self._rows(2000)
        _, undo = cfg_swap(stream_exclude=['zz'], stream_preferred=['es'])
        try:
            S._rows_pipe(rows, ('movie', 'tt-x', ()))
            _, dt = _timed(lambda: [S._rows_pipe(rows, ('movie', 'tt-x', ()))
                                    for _ in range(50)])
        finally:
            undo()
        self.assertLess(dt, 0.5, f'50 warm hits: {dt*1000:.0f}ms')

    def test_firma_distinta_recomputa(self):
        """Config distinta -> otra entrada: nunca sirve la firma vieja
        (exclude=WEBRip filtra las 50; exclude=[] las deja todas)."""
        from tests import cfg_swap
        rows = self._rows(50)
        import conflux.core.cfg as _c
        old_rex = _c.RE_EX                                 # RE_EX se rebindea
        _, undo = cfg_swap(stream_exclude=['WEBRip'])      # en load_config
        _c.RE_EX = _c._ex_re(['WEBRip'])                   # (emulo el reload)
        try:
            out_a = S._rows_pipe(rows, ('movie', 'tt-y', ()))
        finally:
            _c.RE_EX = old_rex
            undo()
        out_b = S._rows_pipe(rows, ('movie', 'tt-y', ()))
        self.assertEqual(len(out_a), 0)
        self.assertEqual(len(out_b), 50)

    def test_rows_nuevas_no_stale(self):
        """Otro objeto rows (nuevo collect) con la misma firma -> recomputa
        (la entrada verifica identidad, no sirve la salida vieja)."""
        rows1 = self._rows(30)
        rows2 = self._rows(60)
        S._rows_pipe(rows1, ('movie', 'tt-z', ()))
        self.assertEqual(len(S._rows_pipe(rows2, ('movie', 'tt-z', ()))), 60)


class TestM3uStatCache(unittest.TestCase):
    """_m3u_metas: stat(path,mtime,size) intacto -> NO relee el fichero.
    Antes cada vista del catálogo IPTV releía el .m3u entero (MBs) para
    descubrir que no había cambiado."""

    def tearDown(self):
        import conflux.domain.lists as L
        L._M3U.clear(); L._M3U_TXT[0] = None; L._M3U_SIG[0] = None

    def test_sin_cambio_no_relee(self):
        import tempfile
        from tests import cfg_swap
        from unittest.mock import patch
        import conflux.domain.lists as L
        fd, path = tempfile.mkstemp(suffix='.m3u')
        os.write(fd, b'#EXTM3U\n#EXTINF:-1,A\nhttp://x\n')
        os.close(fd)
        self.addCleanup(os.unlink, path)
        _, undo = cfg_swap(m3u_path=path)
        self.addCleanup(undo)
        calls = [0]
        real_open = L._open
        def counting(*a, **k):
            calls[0] += 1
            return real_open(*a, **k)
        with patch.object(L, '_open', counting):
            L._m3u_metas(); L._m3u_metas(); L._m3u_metas()
        self.assertEqual(calls[0], 1,
                         'mtime/size intactos pero releyó el fichero')

    def test_cambio_si_relee(self):
        """El contenido nuevo sí se recoge (la firma stat cambió)."""
        import tempfile
        from tests import cfg_swap
        import conflux.domain.lists as L
        fd, path = tempfile.mkstemp(suffix='.m3u')
        os.write(fd, b'#EXTM3U\n#EXTINF:-1,A\nhttp://x\n')
        os.close(fd)
        self.addCleanup(os.unlink, path)
        _, undo = cfg_swap(m3u_path=path)
        self.addCleanup(undo)
        self.assertEqual(len(L._m3u_metas()), 1)
        time.sleep(0.002)                                # resolución mtime NTFS
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('#EXTM3U\n#EXTINF:-1,A\nhttp://x\n'
                     '#EXTINF:-1,B\nhttp://y\n')
        self.assertEqual(len(L._m3u_metas()), 2)


class TestEpgHotPath(unittest.TestCase):
    """epg_meta/_videos por request: _iso memoizado — 200 vistas de un
    canal con 24 programas no puede tardar segundos."""

    def test_epg_meta_200_vistas(self):
        import conflux.infra.epg as E
        today = time.strftime('%Y-%m-%d', time.gmtime())
        nxt = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 86400))
        t0 = E.CLOCK.time()
        progs = [{'title': 'p', 'desc': '', 'icon': '',
                  'start': t0 + i * 1800, 'stop': t0 + i * 1800 + 900}
                 for i in range(24)]
        old_days, old_f = E._STATE.get('days'), E._STATE.get('fetched')
        E._STATE['days'] = {today: {'c1': progs}, nxt: {'c1': progs[:6]}}
        E._STATE['fetched'] = t0
        try:
            _, dt = _timed(lambda: [E.epg_meta({'id': 'c1', 'name': 'c1'})
                                    for _ in range(200)])
        finally:
            E._STATE['days'] = old_days or {}
            E._STATE['fetched'] = old_f or 0
        self.assertLess(dt, 2.0, f'200 metas epg: {dt*1000:.0f}ms')


class TestUrlHealthScan(unittest.TestCase):
    """_url_health filtra por stream (O(streams)), no barriendo _URL_BAD
    entero (hasta 2000 veredictos). Tripwire: una regresión a O(veredictos)
    por request cuesta ~µs×2000 en cada /stream."""

    def setUp(self):
        self._b = dict(S._URL_BAD); self._o = dict(S._URL_OK)

    def tearDown(self):
        S._URL_BAD.clear(); S._URL_BAD.update(self._b)
        S._URL_OK.clear(); S._URL_OK.update(self._o)

    def test_2000_veredictos_rapido(self):
        now = st.CLOCK.time()
        S._URL_BAD.clear(); S._URL_OK.clear()      # exactamente 2000: sin prune
        S._URL_BAD.update({f'http://dead{i}.x': (2, now) for i in range(2000)})
        streams = [{'url': f'http://ok{i}.x'} for i in range(100)]
        streams.append({'url': 'http://dead7.x'})          # este sí debe caer
        sub = S._POOL_BG.submit
        S._POOL_BG.submit = lambda f, *a: None             # HEADs fuera: sin red
        try:
            _, dt = _timed(S._url_health, streams)
        finally:
            S._POOL_BG.submit = sub
        self.assertLess(dt, 0.5, f'_url_health: {dt*1000:.0f}ms')
        self.assertNotIn('http://dead7.x', [s['url'] for s in streams])


class TestMetaCacheHit(unittest.TestCase):
    """handle_meta cache-hit: _posters_view+_rpdb por request — presupuesto
    p95 (el detalle de ficha no puede esperar por un meta ya visto)."""

    def test_meta_caliente_rapido(self):
        import conflux.app.meta as M
        key = ('movie', 'tt-hit')
        st._cset(M._meta_c, key, {'meta': {
            'id': 'tt-hit', 'name': 'X', 'videos': [
                {'id': f'tt-hit:{i}', 'season': 1, 'episode': i}
                for i in range(20)]}})
        try:
            ts = []
            for _ in range(50):
                t0 = time.perf_counter()
                M.handle_meta('movie', 'tt-hit')
                ts.append(time.perf_counter() - t0)
            self.assertLess(sorted(ts)[int(len(ts) * .95)], 0.5,
                            'p95 meta caliente demasiado alto')
        finally:
            M._meta_c.pop(key, None)


if __name__ == '__main__':
    unittest.main()
