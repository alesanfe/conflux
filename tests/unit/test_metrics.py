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


if __name__ == '__main__':
    unittest.main()
