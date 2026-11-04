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
                patch.object(bg, 'epg_kick') as kick, \
                patch.object(bg, '_trakt_sync') as ts:
            p.submit.side_effect = [futs, futs]
            bg._prefetch_catalogs()
        self.assertEqual(p.submit.call_count, 2)          # un futuro por catálogo
        kick.assert_called_once()
        ts.assert_called_once()
        self.assertFalse(bg._prefetching.is_set())        # flag liberada en finally


class TestTraktSync(unittest.TestCase):
    def setUp(self):
        self._cfg = dict(bg.CFG)

    def tearDown(self):
        bg.CFG.clear()
        bg.CFG.update(self._cfg)

    def test_sin_token_no_subproceso(self):
        bg.CFG['trakt_access_token'] = ''
        with patch.object(bg.subprocess, 'run') as r:
            bg._trakt_sync()
            r.assert_not_called()

    def test_con_token_lanza_y_loguea(self):
        bg.CFG['trakt_access_token'] = 'tok'
        with patch.object(bg.os.path, 'exists', return_value=True), \
                patch.object(bg.subprocess, 'run',
                             return_value=MagicMock(returncode=0, stdout='ok')) as r, \
                patch.object(bg, 'log_ev') as le:
            bg._trakt_sync()
        r.assert_called_once()
        le.assert_called_once_with('trakt_sync', rc=0, out='ok')

    def test_rc_no_cero_se_loguea_no_excepciona(self):
        """check=False: rc!=0 NO lanza — el scrobble fallido queda como dato
        (mutante check=True lo convertiría en trakt_sync_err)."""
        bg.CFG['trakt_access_token'] = 'tok'
        with patch.object(bg.os.path, 'exists', return_value=True), \
                patch.object(bg.subprocess, 'run',
                             return_value=MagicMock(returncode=3, stdout='',
                                                    stderr='locked')) as r, \
                patch.object(bg, 'log_ev') as le:
            bg._trakt_sync()
        self.assertEqual(r.call_args[1].get('check'), False)
        le.assert_called_once_with('trakt_sync', rc=3, out='locked')

    def test_kwargs_subproceso(self):
        """capture_output/text/timeout son contrato (mutantes: sin captura
        -> out=''; text->False -> bytes en el log; timeout distinto)."""
        bg.CFG['trakt_access_token'] = 'tok'
        with patch.object(bg.os.path, 'exists', return_value=True), \
                patch.object(bg.subprocess, 'run',
                             return_value=MagicMock(returncode=0, stdout='ok')) as r, \
                patch.object(bg, 'log_ev'):
            bg._trakt_sync()
        kw = r.call_args[1]
        self.assertIs(kw['capture_output'], True)
        self.assertIs(kw['text'], True)
        self.assertEqual(kw['timeout'], 300)

    def test_out_cap_200(self):
        """stdout se loguea strip()[:200] (200+1@32)."""
        bg.CFG['trakt_access_token'] = 'tok'
        with patch.object(bg.os.path, 'exists', return_value=True), \
                patch.object(bg.subprocess, 'run',
                             return_value=MagicMock(returncode=0,
                                                    stdout='x' * 300)), \
                patch.object(bg, 'log_ev') as le:
            bg._trakt_sync()
        self.assertEqual(le.call_args[1]['out'], 'x' * 200)

    def test_err_cap_100(self):
        """repr(e)[:100] en el except de _trakt_sync (100+1)."""
        bg.CFG['trakt_access_token'] = 'tok'
        with patch.object(bg.os.path, 'exists', return_value=True), \
                patch.object(bg.subprocess, 'run',
                             side_effect=OSError('e' * 300)), \
                patch.object(bg, 'log_ev') as le:
            bg._trakt_sync()
        self.assertEqual(len(le.call_args[1]['err']), 100)

    def test_excepcion_no_muere_daemon(self):
        bg.CFG['trakt_access_token'] = 'tok'
        with patch.object(bg.os.path, 'exists', return_value=True), \
                patch.object(bg.subprocess, 'run', side_effect=OSError('boom')), \
                patch.object(bg, 'log_ev') as le:
            bg._trakt_sync()                            # no propaga
        le.assert_called_once()


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
                patch.object(bg, 'epg_kick') as kick, \
                patch.object(bg, '_trakt_sync') as ts:
            with self.assertRaises(_StopLoop):
                bg._refresher()
        rm.assert_called_once()
        kick.assert_called_once()
        ts.assert_called_once()
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
                patch.object(bg, '_trakt_sync',
                             side_effect=RuntimeError('x' * 300)), \
                patch.object(bg, 'log_ev') as le:
            with self.assertRaises(_StopLoop):
                bg._refresher()                       # 2 ticks pese a todo fallando
        self.assertEqual(n[0], 3)
        # repr(e)[:100] en los 3 except del ciclo (100+1@61/65/69)
        errs = {c.args[0]: len(c[1]['err']) for c in le.call_args_list}
        self.assertEqual(errs, {'refresh_err': 100, 'epg_err': 100,
                                'trakt_sync_err': 100})


if __name__ == '__main__':
    unittest.main()
