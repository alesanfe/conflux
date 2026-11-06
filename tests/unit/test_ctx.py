import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from conflux.core.ctx import AppContext
from conflux.core import state as st


class TestCtxIsolation(unittest.TestCase):
    def test_two_contexts_isolated(self):
        c1, c2 = AppContext(), AppContext()
        c1.sources.sources.append('S1')
        c1.breakers.fails['h'] = (3, 0)
        c1.cfg['k'] = 1
        self.assertEqual(c2.sources.sources, [])
        self.assertEqual(c2.breakers.fails, {})
        self.assertEqual(c2.cfg, {})

    def test_mutation_not_shared(self):
        """Modificar una fuente en un contexto no modifica otro."""
        c1, c2 = AppContext(), AppContext()
        c1.sources.sources.append({'name': 'X'})
        c1.caches['stream']['k'] = (0, 'v')
        self.assertNotIn({'name': 'X'}, c2.sources.sources)
        self.assertEqual(c2.caches['stream'], {})

    def test_destroy_and_recreate(self):
        c1 = AppContext()
        c1.shutdown_pools()
        del c1
        c2 = AppContext()
        self.assertFalse(c2.closed)

    def test_shutdown_idempotent(self):
        c = AppContext()
        c.shutdown_pools()
        c.shutdown_pools()                     # no lanza
        self.assertTrue(c.closed)

    def test_pool_recovery_after_close(self):
        c = AppContext()
        c.register_pool(ThreadPoolExecutor(max_workers=1))
        c.shutdown_pools()
        self.assertTrue(c.closed)
        c.register_pool(ThreadPoolExecutor(max_workers=1))   # recovery
        self.assertFalse(c.closed)

    def test_pool_actually_closed(self):
        c = AppContext()
        p = c.register_pool(ThreadPoolExecutor(max_workers=1))
        c.shutdown_pools()
        with self.assertRaises(RuntimeError):
            p.submit(lambda: 1)

    def test_concurrent_failure_registry(self):
        c = AppContext()
        def hammer(i):
            for _ in range(200):
                h = c.failures.health.setdefault(
                    f's{i}', {'ok': 0, 'fail': 0, 'streak': 0})
                h['fail'] += 1
        ts = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(c.failures.health), 8)
        for i in range(8):
            self.assertEqual(c.failures.health[f's{i}']['fail'], 200)

    def test_concurrent_breakers(self):
        c = AppContext()
        def hammer(i):
            for n in range(200):
                c.breakers.fails[f'h{i % 4}'] = (n, n)
        ts = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(c.breakers.fails), 4)


class TestDefaultCtx(unittest.TestCase):
    """El CTX por defecto comparte objetos con los nombres de módulo (in-situ)."""

    def test_default_context_is_ctx(self):
        from conflux.core import ctx as ctxmod
        self.assertIs(ctxmod.default_context(), st.CTX)

    def test_ctx_binds_module_state(self):
        self.assertIs(st.CTX.sources.sources, st.SOURCES)
        self.assertIs(st.CTX.sources.manifests, st.manifests)
        self.assertIs(st.CTX.breakers.fails, st._fails)
        self.assertIs(st.CTX.breakers.cooldown, st.COOLDOWN)
        self.assertIs(st.CTX.failures.health, st.HEALTH)
        self.assertIs(st.CTX.cfg, st.CFG)
        self.assertIs(st.CTX.caches['stream'], st._cache)
        self.assertIs(st.CTX.caches['meta'], st._meta_c)
        self.assertIs(st.CTX.caches['catalog'], st._cat_c)
        self.assertIs(st.CTX.sessions, st._SESS)
        self.assertIs(st.CTX.metrics.rtime, st._rtime)
        self.assertIs(st.CTX.metrics.served, st._SERVED)

    def test_mutation_visible_via_module(self):
        st.CTX.breakers.fails['ctx-test-host'] = (1, 0)
        self.assertIn('ctx-test-host', st._fails)


if __name__ == '__main__':
    unittest.main()
