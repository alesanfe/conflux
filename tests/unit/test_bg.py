# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.app.bg — prefetch + refresher."""
import unittest
from unittest.mock import MagicMock, patch

from conflux.app import bg


class TestPrefetch(unittest.TestCase):
    def test_no_reentra_si_ya_corre(self):
        bg._prefetching.set()
        try:
            with patch.object(bg, '_POOL_BG') as p:
                bg._prefetch_catalogs()
                p.submit.assert_not_called()
        finally:
            bg._prefetching.clear()

    def test_prefetch_envia_catalogos_y_kicks(self):
        futs = MagicMock()
        futs.result.return_value = None
        with patch.object(bg, '_POOL_BG') as p, \
                patch.object(bg, 'build_manifest',
                             return_value={'catalogs': [{'type': 'movie', 'id': 'c1'},
                                                        {'type': 'series', 'id': 'c2'}]}), \
                patch.object(bg, 'epg_kick') as kick:
            p.submit.side_effect = [futs, futs]
            bg._prefetch_catalogs()
        self.assertEqual(p.submit.call_count, 2)          # un futuro por catálogo
        kick.assert_called_once()
        self.assertFalse(bg._prefetching.is_set())        # flag liberada en finally


class _StopLoop(Exception):
    pass


class TestRefresher(unittest.TestCase):
    def test_un_ciclo_llama_todo_y_sobrevive_errores(self):
        sleeps = []
        def fake_sleep(s):
            sleeps.append(s)
            if len(sleeps) > 1:
                raise _StopLoop                       # corta el while en el 2º tick
        with patch.object(bg.time, 'sleep', fake_sleep), \
                patch.object(bg, 'refresh_manifests') as rm, \
                patch.object(bg, 'epg_kick') as kick:
            with self.assertRaises(_StopLoop):
                bg._refresher()
        rm.assert_called_once()
        kick.assert_called_once()
        self.assertEqual(sleeps[0], 1800)             # cadencia 30min

    def test_error_en_refresh_no_mata_el_loop(self):
        n = [0]
        def fake_sleep(s):
            n[0] += 1
            if n[0] > 2:
                raise _StopLoop
        with patch.object(bg.time, 'sleep', fake_sleep), \
                patch.object(bg, 'refresh_manifests',
                             side_effect=RuntimeError('x' * 300)), \
                patch.object(bg, 'epg_kick',
                             side_effect=RuntimeError('x' * 300)), \
                patch.object(bg, 'log_ev') as le:
            with self.assertRaises(_StopLoop):
                bg._refresher()                       # 2 ticks pese a todo fallando
        self.assertEqual(n[0], 3)
        # repr(e)[:100] en los except del ciclo (100+1)
        errs = {c.args[0]: len(c[1]['err']) for c in le.call_args_list}
        self.assertEqual(errs, {'refresh_err': 100, 'epg_err': 100})


if __name__ == '__main__':
    unittest.main()
