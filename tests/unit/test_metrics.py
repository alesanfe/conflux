# pylint: disable=missing-docstring,protected-access,wrong-import-position
"""Unit tests conflux.web.metrics — exposición Prometheus."""
import time
import unittest
from unittest.mock import MagicMock

import conflux.core.state as st
import conflux.web.metrics as mx


class TestPct(unittest.TestCase):
    def test_vacio_cero(self):
        """`if not vals` (not-elim@31: con vals vacío el mutante cae a s[-1]
        y explota; con vals lleno devolvería 0.0 siempre)."""
        self.assertEqual(mx._pct([], .5), 0.0)

    def test_percentiles_exactos(self):
        """min(len-1, int(q*len)) — Sub/Mult@34 cambian el índice escogido;
        .5/.95/.99+1 distinguen el cuantil pedido."""
        vals = list(range(1, 201))                        # 200 muestras
        self.assertEqual(mx._pct(vals, .5), 101)          # int(.5*200)=100 -> s[100]
        self.assertEqual(mx._pct(vals, .95), 191)
        self.assertEqual(mx._pct(vals, .99), 199)
        self.assertEqual(mx._pct(vals, .0), 1)

    def test_pct_desordenados(self):
        self.assertEqual(mx._pct([9, 1, 5], .5), 5)


class TestMetricsText(unittest.TestCase):
    def setUp(self):
        self._old = {n: getattr(st, n).copy() if hasattr(getattr(st, n), 'copy')
                     else list(getattr(st, n))
                     for n in ('HEALTH', 'COOLDOWN', '_SERVED', '_rtime',
                               '_fails', '_cache', '_meta_c', '_cat_c')}
        self._rts = {k: list(v) for k, v in st._rts.items()}
        self._man = list(st.manifests)
        self._src = list(st.SOURCES)
        self._now = time.time()

    def tearDown(self):
        for n, v in self._old.items():
            o = getattr(st, n)
            if hasattr(o, 'clear'):
                o.clear(); o.update(v)
        st._rts.clear(); st._rts.update({k: type(v)(v) for k, v in self._rts.items()})
        st.manifests[:] = self._man
        st.SOURCES[:] = self._src

    def test_lineas_por_seccion(self):
        st.HEALTH.update({'Src': {'ok': 3, 'fail': 2}})
        st.COOLDOWN['Cd'] = (self._now + 60, 0)
        st._SERVED['Sv'] = {'n': 7, 'res': {'4k': 2}}
        st.manifests[:] = [MagicMock(), MagicMock()]
        st._rtime['/x'] = (4, 0.8, 0.5)                   # n, total, max
        st._rts['/x'] = __import__('collections').deque([0.1, 0.2, 0.3, 0.4])
        st._fails['h1'] = (5, 0.0)
        st._cache['k'] = 1
        out = mx._metrics_text()
        self.assertIn('conflux_source_requests_total{source="Src",result="ok"} 3', out)
        self.assertIn('conflux_source_requests_total{source="Src",result="fail"} 2', out)
        # Sub@47: cd[0]-now -> cd[0]+now daría una cifra enorme
        left = [ln for ln in out.splitlines()
                if ln.startswith('conflux_source_cooldown_seconds{source="Cd"}')][0]
        self.assertAlmostEqual(float(left.rsplit(' ', 1)[1]), 60, delta=2)
        self.assertIn('conflux_streams_served_total{source="Sv"} 7', out)
        self.assertIn('conflux_streams_byres_total{source="Sv",res="4k"} 2', out)
        self.assertIn('conflux_source_up 2', out)
        # Div@62: avg = t/n = 0.8/4 = 0.2 (mutante t*n = 3.2)
        self.assertIn('conflux_route_latency_ms{route="/x",quantile="avg"} 0.2', out)
        self.assertIn('conflux_host_fail_streak{host="h1"} 5', out)
        self.assertIn('conflux_cache_entries{cache="stream"} ', out)
        self.assertIn('conflux_poster{kind="poster_dead"} ', out)   # META-007
        self.assertTrue(out.endswith('\n'))

    def test_percentiles_en_texto(self):
        """q=.5/.95/.99 en el texto (0.5+1@65 etc.: el mutante pediría 1.5
        -> max, indistinguible solo si no se verifica el valor)."""
        st._rtime['/y'] = (200, 40.0, 10.0)
        st._rts['/y'] = __import__('collections').deque(range(200))  # ms: 0..199
        out = mx._metrics_text()
        self.assertIn('quantile="0.5"} 100.0', out)       # s[100]=100ms
        self.assertIn('quantile="0.95"} 190.0', out)
        self.assertIn('quantile="0.99"} 198.0', out)
        self.assertIn('quantile="max"} 10.0', out)

    def test_esc_escapa(self):
        st.HEALTH['A"B\\C'] = {'ok': 1}
        out = mx._metrics_text()
        self.assertIn('source="A\\"B\\\\C"', out)


class TestHist(unittest.TestCase):
    """OBS-002: rollup diario de contadores -> .metrics-history.json.
    Vive en conflux.app.obs (capa app — web la lee, al revés no)."""

    def setUp(self):
        import conflux.app.obs as obs
        self._obs = obs
        self._h = dict(obs._HIST)
        self._p = dict(obs._PREV)
        self._hl = dict(st.HEALTH)
        self._sv = {k: dict(v) for k, v in st._SERVED.items()}

    def tearDown(self):
        self._obs._HIST.clear(); self._obs._HIST.update(self._h)
        self._obs._PREV.clear(); self._obs._PREV.update(self._p)
        st.HEALTH.clear(); st.HEALTH.update(self._hl)
        st._SERVED.clear(); st._SERVED.update(self._sv)

    def _snap(self, path):
        from unittest.mock import patch
        with patch.object(self._obs, '_HIST_PATH', path):
            self._obs._hist_snapshot()

    def test_rollup_delta_diario(self):
        import json as _j
        import os
        import tempfile
        st.HEALTH.clear()                              # determinista: solo mis contadores
        st._SERVED.clear()
        st.HEALTH['S'] = {'ok': 10, 'fail': 2}
        st._SERVED['S'] = {'n': 5, 'res': {}}
        obs = self._obs
        obs._PREV.clear()
        obs._HIST.clear()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'h.json')
            self._snap(p)                                # snapshot 1: todo delta
            day = next(iter(obs._HIST))
            self.assertEqual(obs._HIST[day].get('req_ok'), 10)
            self.assertEqual(obs._HIST[day].get('req_fail'), 2)
            self.assertEqual(obs._HIST[day].get('streams'), 5)
            with open(p, encoding='utf-8') as f:
                raw = _j.load(f)                       # persistido atómico
            self.assertIn(day, raw['days'])
            st.HEALTH['S']['ok'] += 4                  # +4 desde el último snap
            self._snap(p)
            self.assertEqual(obs._HIST[day]['req_ok'], 14)   # acumula delta

    def test_anillo_90_dias(self):
        import os
        import tempfile
        obs = self._obs
        obs._HIST.clear()
        obs._HIST.update({f'2000-01-{d:02d}': {'req_ok': d} for d in range(1, 32)})
        for i in range(60):                            # 91 días en total
            obs._HIST[f'2000-03-{(i % 28) + 1:02d}x{i:02d}'] = {}
        with tempfile.TemporaryDirectory() as td:
            self._snap(os.path.join(td, 'h.json'))
        self.assertLessEqual(len(obs._HIST), obs._HIST_KEEP)

    def test_load_tolerante(self):
        import os
        import tempfile
        obs = self._obs
        obs._HIST.clear()
        obs._PREV.clear()
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'h.json')
            with open(p, 'w', encoding='utf-8') as f:
                f.write('{roto')
            from unittest.mock import patch
            with patch.object(obs, '_HIST_PATH', p):
                obs._hist_load()                       # no crashea, queda vacío
            self.assertEqual(obs._HIST, {})
            _j = __import__('json')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(_j.dumps(
                    {'days': {'2020-01-01': {'req_ok': 7}},
                     'prev': {'req_ok': 7}}))
            with patch.object(obs, '_HIST_PATH', p):
                obs._hist_load()
            self.assertEqual(obs._HIST['2020-01-01'], {'req_ok': 7})
            self.assertEqual(obs._PREV['req_ok'], 7)


class TestDiagJson(unittest.TestCase):
    """OBS-008/009: /admin/diag — snapshot JSON estructurado, sin secretos."""

    def test_estructura_y_whitelist(self):
        st.HEALTH['Src'] = {'ok': 3, 'fail': 1, 'err': 'x', 'streak': 0}
        st._SERVED['Src'] = {'n': 2, 'res': {'4k': 1}}
        st._rtime['/stream'] = (4, 0.4, 0.2)
        st._fails['h'] = (2, 0.0)
        try:
            d = mx._diag_json()
        finally:
            st.HEALTH.pop('Src', None)
            st._SERVED.pop('Src', None)
            st._rtime.pop('/stream', None)
            st._fails.pop('h', None)
        for k in ('version', 'uptime_s', 'ts', 'sources_up', 'sources_total',
                  'sources', 'route_latency', 'host_fail_streaks', 'caches',
                  'posters', 'epg', 'history_30d'):
            self.assertIn(k, d)
        self.assertEqual(d['sources']['Src']['ok'], 3)
        self.assertEqual(d['sources']['Src']['streams'], 2)
        self.assertEqual(d['route_latency']['/stream']['n'], 4)
        self.assertIn('/stream', d['route_latency'])
        self.assertEqual(d['host_fail_streaks']['h'], 2)
        # QUAL-014: ningún valor de config/tokens aparece en el JSON
        blob = __import__('json').dumps(d)
        for k in ('admin_token', 'debrid_key', 'rpdb_key', 'opensubtitles'):
            self.assertNotIn(k, blob)


if __name__ == '__main__':
    unittest.main()
