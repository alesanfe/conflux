"""Unit tests conflux.core.state — cachés, health, mutación in situ."""
import time
import unittest

import conflux.core.state as st


class TestCache(unittest.TestCase):
    def setUp(self):
        self.c = {}

    def test_cget_miss(self):
        self.assertIsNone(st._cget(self.c, 'k'))

    def test_cset_cget_hit(self):
        st._cset(self.c, 'k', {'a': 1})
        self.assertEqual(st._cget(self.c, 'k'), {'a': 1})

    def test_cget_expira(self):
        self.c['k'] = (time.time() - st.CACHE_TTL - 1, 'v')
        self.assertIsNone(st._cget(self.c, 'k'))

    def test_cstale_sobrevive_ttl(self):
        self.c['k'] = (time.time() - st.CACHE_TTL - 1, 'v')
        self.assertEqual(st._cstale(self.c, 'k'), 'v')

    def test_cstale_muere_a_24h(self):
        self.c['k'] = (time.time() - st.STALE_MAX - 1, 'v')
        self.assertIsNone(st._cstale(self.c, 'k'))

    def test_cset_evict_fifo(self):
        for i in range(410):
            st._cset(self.c, i, i)
        self.assertLessEqual(len(self.c), 401)


class TestHealth(unittest.TestCase):
    def test_health_registra(self):
        st._health('T', True, '', 5)
        h = st.HEALTH['T']
        self.assertEqual(h['ok'], 1)
        self.assertEqual(h['n'], 5)

    def test_health_streak_fallos(self):
        st._health('T2', False, 'err', 0)
        st._health('T2', False, 'err', 0)
        self.assertEqual(st.HEALTH['T2']['streak'], 2)
        st._health('T2', True, '', 0)
        self.assertEqual(st.HEALTH['T2']['streak'], 0)


class TestCfgDefaults(unittest.TestCase):
    def test_defaults_cargados(self):
        for k in ('calendar_months_ahead', 'stream_langs'):
            self.assertIn(k, st.CFG)


if __name__ == '__main__':
    unittest.main()
