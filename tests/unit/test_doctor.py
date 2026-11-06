import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from tests import patch_fetch          # noqa: F401
from conflux.app import doctor
from conflux.core import cfg as cfg_mod


class TestAddonState(unittest.TestCase):
    def test_not_attempted(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(doctor, '_addon_state_file',
                               return_value=os.path.join(d, 'x.json')), \
             mock.patch('conflux.app.install._marker',
                        return_value=os.path.join(d, 'addon.ok')):
            self.assertEqual(doctor.addon_state()['state'], 'not_attempted')

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'st.json')
            with mock.patch.object(doctor, '_addon_state_file', return_value=f):
                doctor.set_addon_state('registered')
                self.assertEqual(doctor.addon_state()['state'], 'registered')
                doctor.set_addon_state('retry_pending', last_error='boom',
                                       attempts=3)
                st = doctor.addon_state()
                self.assertEqual(st['state'], 'retry_pending')
                self.assertEqual(st['attempts'], 3)
                self.assertEqual(st['last_error'], 'boom')
                self.assertIn('ts', st)
                self.assertIn('version', st)

    def test_invalid_state_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'st.json')
            with mock.patch.object(doctor, '_addon_state_file', return_value=f):
                doctor.set_addon_state('inventado')
                self.assertFalse(os.path.exists(f))

    def test_legacy_marker(self):
        with tempfile.TemporaryDirectory() as d:
            mk = os.path.join(d, 'addon.ok')
            open(mk, 'w').close()
            with mock.patch.object(doctor, '_addon_state_file',
                                   return_value=os.path.join(d, 'x.json')), \
                 mock.patch('conflux.app.install._marker', return_value=mk):
                self.assertEqual(doctor.addon_state()['state'], 'registered')

    def test_corrupt_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'st.json')
            with open(f, 'w') as fh:
                fh.write('{corrupto')
            with mock.patch.object(doctor, '_addon_state_file', return_value=f), \
                 mock.patch('conflux.app.install._marker',
                            return_value=os.path.join(d, 'nope')):
                self.assertEqual(doctor.addon_state()['state'], 'not_attempted')


class TestStatus(unittest.TestCase):
    def test_json_stable(self):
        buf = io.StringIO()
        with mock.patch.object(doctor, '_http', return_value=(None, None)):
            rc = doctor.run_status(json_out=True, out=buf.write)
        self.assertEqual(rc, 0)
        st = json.loads(buf.getvalue())
        for k in ('version', 'running', 'sources', 'breakers_open', 'addon',
                  'log', 'config'):
            self.assertIn(k, st)

    def test_text(self):
        buf = io.StringIO()
        with mock.patch.object(doctor, '_http', return_value=(200, {})):
            rc = doctor.run_status(out=buf.write)
        self.assertEqual(rc, 0)
        self.assertIn('conflux', buf.getvalue())
        self.assertIn('fuentes', buf.getvalue())


class TestDoctor(unittest.TestCase):
    def test_exit_warn(self):
        buf = io.StringIO()
        checks = [('a', 'ok', 'x'), ('b', 'warn', 'y')]
        with mock.patch.object(doctor, 'collect_doctor', return_value=checks):
            rc = doctor.run_doctor(out=buf.write)
        self.assertEqual(rc, 1)
        self.assertIn('AVISO', buf.getvalue())

    def test_exit_service_down(self):
        checks = [('service', 'fail', 'caído')]
        with mock.patch.object(doctor, 'collect_doctor', return_value=checks):
            rc = doctor.run_doctor(out=lambda *_: None)
        self.assertEqual(rc, 3)

    def test_exit_config_invalid(self):
        checks = [('config', 'fail', 'rota')]
        with mock.patch.object(doctor, 'collect_doctor', return_value=checks):
            rc = doctor.run_doctor(out=lambda *_: None)
        self.assertEqual(rc, 2)

    def test_json(self):
        buf = io.StringIO()
        checks = [('a', 'ok', 'x')]
        with mock.patch.object(doctor, 'collect_doctor', return_value=checks):
            doctor.run_doctor(json_out=True, out=buf.write)
        rep = json.loads(buf.getvalue())
        self.assertEqual(rep['checks'][0]['level'], 'ok')
        self.assertIn('version', rep)


class TestAddonStateEdge(unittest.TestCase):
    def test_state_inventado_en_fichero_cae_a_marker(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'st.json')
            with open(f, 'w') as fh:
                json.dump({'state': 'no_existe'}, fh)
            with mock.patch.object(doctor, '_addon_state_file', return_value=f), \
                 mock.patch('conflux.app.install._marker',
                            return_value=os.path.join(d, 'nope')):
                self.assertEqual(doctor.addon_state()['state'], 'not_attempted')

    def test_marker_explota_cae_a_not_attempted(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(doctor, '_addon_state_file',
                               return_value=os.path.join(d, 'x.json')), \
             mock.patch('conflux.app.install._marker',
                        side_effect=RuntimeError('boom')):
            self.assertEqual(doctor.addon_state()['state'], 'not_attempted')

    def test_set_state_oserror_tragado(self):
        with mock.patch.object(doctor, '_addon_state_file',
                               return_value='/no/existe/dir/x.json'), \
             mock.patch('conflux.core.state._atomic_json',
                        side_effect=OSError('ro')):
            doctor.set_addon_state('failed')   # no propaga


class TestHttpProbe(unittest.TestCase):
    def test_ok(self):
        import urllib.request
        r = mock.MagicMock()
        r.status = 200
        r.__enter__ = lambda s: s
        r.__exit__ = lambda s, *a: False
        r.read = lambda: b'{"ok":1}'
        with mock.patch.object(urllib.request, 'urlopen',
                               return_value=r):
            code, body = doctor._http('/health')
        self.assertEqual(code, 200)

    def test_http_error_devuelve_code(self):
        import urllib.error
        import urllib.request
        with mock.patch.object(urllib.request, 'urlopen',
                               side_effect=urllib.error.HTTPError(
                                   'u', 404, 'nf', {}, None)):
            code, body = doctor._http('/x')
        self.assertEqual(code, 404)
        self.assertIsNone(body)

    def test_excepcion_generica_none(self):
        import urllib.request
        with mock.patch.object(urllib.request, 'urlopen',
                               side_effect=ConnectionRefusedError()):
            code, body = doctor._http('/x')
        self.assertIsNone(code)
        self.assertIsNone(body)


class TestPortFree(unittest.TestCase):
    def test_puerto_ocupado_false(self):
        import socket
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        try:
            self.assertFalse(doctor._port_free(s.getsockname()[1]))
        finally:
            s.close()

    def test_puerto_libre_true(self):
        import socket
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        self.assertTrue(doctor._port_free(port))


class TestCfgCheck(unittest.TestCase):
    def test_no_existe_warn(self):
        with mock.patch.object(doctor.os.path, 'exists', return_value=False):
            lvl, msg = doctor._cfg_check()
        self.assertEqual(lvl, 'warn')
        self.assertIn('no existe', msg)

    def test_json_roto_fail(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'config.json')
            with open(f, 'w') as fh:
                fh.write('{roto')
            with mock.patch.object(doctor, '_ROOT', d):
                lvl, msg = doctor._cfg_check()
        self.assertEqual(lvl, 'fail')

    def test_ok(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'config.json')
            with open(f, 'w') as fh:
                json.dump({'a': 1}, fh)
            os.chmod(f, 0o600)   # POSIX: el check exige perms 600
            with mock.patch.object(doctor, '_ROOT', d):
                lvl, msg = doctor._cfg_check()
        self.assertEqual(lvl, 'ok')

    @unittest.skipIf(sys.platform == 'win32', 'perms POSIX')
    def test_perms_abiertas_warn(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'config.json')
            with open(f, 'w') as fh:
                json.dump({'a': 1}, fh)
            os.chmod(f, 0o644)
            with mock.patch.object(doctor, '_ROOT', d):
                lvl, msg = doctor._cfg_check()
        self.assertEqual(lvl, 'warn')
        self.assertIn('perms', msg)


class TestCollectStatusEdge(unittest.TestCase):
    def test_cache_age_none_sin_fichero(self):
        with mock.patch.object(doctor, '_http', return_value=(None, None)), \
             mock.patch.object(doctor.os.path, 'getmtime',
                               side_effect=OSError('no file')):
            st = doctor.collect_status()
        self.assertIsNone(st['cache_age_s'])
        self.assertFalse(st['running'])

    def test_degraded_y_breakers(self):
        import conflux.core.state as st
        with mock.patch.object(doctor, '_http', return_value=(200, {})), \
             mock.patch.object(doctor, '_fails',
                               {'h.lan': (5, doctor.time.time())}), \
             mock.patch.object(st, 'HEALTH', {'src': {'streak': 4}}), \
             mock.patch.object(doctor.os.path, 'getmtime',
                               return_value=doctor.time.time() - 10):
            out = doctor.collect_status()
        self.assertEqual(out['breakers_open'], ['h.lan'])
        self.assertIn('src', out['sources']['degraded'])
        self.assertIsNotNone(out['cache_age_s'])

    def test_run_status_texto_con_cache(self):
        buf = io.StringIO()
        st = {'version': '1', 'running': True, 'url': 'u',
              'sources': {'total': 2, 'with_manifest': 1, 'degraded': ['s']},
              'breakers_open': ['h'], 'addon': {'state': 'registered'},
              'cache_age_s': 42, 'log': 'l', 'config': 'c'}
        with mock.patch.object(doctor, 'collect_status', return_value=st):
            rc = doctor.run_status(out=buf.write)
        self.assertEqual(rc, 0)
        txt = buf.getvalue()
        self.assertIn('degradadas: s', txt)
        self.assertIn('h', txt)
        self.assertIn('hace 42s', txt)


class TestRunStatusTextoEdge(unittest.TestCase):
    def test_cache_none_no_imprime_edad(self):
        buf = io.StringIO()
        st = {'version': '1', 'running': False, 'url': 'u',
              'sources': {'total': 0, 'with_manifest': 0, 'degraded': []},
              'breakers_open': [], 'addon': {'state': 'x'},
              'cache_age_s': None, 'log': 'l', 'config': 'c'}
        with mock.patch.object(doctor, 'collect_status', return_value=st):
            doctor.run_status(out=buf.write)
        self.assertNotIn('hace', buf.getvalue())


class TestCfgCheckPosix(unittest.TestCase):
    def test_perms_warn_simulando_posix(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'config.json')
            with open(f, 'w') as fh:
                json.dump({'a': 1}, fh)
            with mock.patch.object(doctor, '_ROOT', d), \
                 mock.patch.object(doctor.sys, 'platform', 'linux'), \
                 mock.patch.object(doctor.os, 'stat',
                                   return_value=mock.MagicMock(
                                       st_mode=0o644)):
                lvl, msg = doctor._cfg_check()
        self.assertEqual(lvl, 'warn')
        self.assertIn('perms', msg)


class TestLogCheck(unittest.TestCase):
    def test_log_no_escribible_warn(self):
        import builtins
        real_open = builtins.open
        def picky(p, *a, **k):
            if p == doctor._LOG_FILE:
                raise OSError('ro')
            return real_open(p, *a, **k)
        with mock.patch.object(doctor, '_http', return_value=(200, {})), \
             mock.patch.object(doctor, '_port_free', return_value=False), \
             mock.patch.object(doctor, 'addon_state',
                               return_value={'state': 'registered'}), \
             mock.patch.object(doctor, 'collect_status', return_value={
                 'version': '1', 'running': True, 'url': 'u',
                 'sources': {'total': 1, 'with_manifest': 1,
                             'degraded': []},
                 'breakers_open': [], 'addon': {}, 'cache_age_s': 1,
                 'log': 'l', 'config': 'c'}), \
             mock.patch('builtins.open', side_effect=picky):
            checks = dict((i, (lv, m)) for i, lv, m in doctor.collect_doctor())
        self.assertEqual(checks['log'][0], 'warn')


class TestCollectDoctor(unittest.TestCase):
    def _st(self, **kw):
        base = {'version': '1', 'running': False, 'url': 'u',
                'sources': {'total': 0, 'with_manifest': 0, 'degraded': []},
                'breakers_open': [], 'addon': {'state': 'not_attempted'},
                'cache_age_s': None, 'log': 'l', 'config': 'c'}
        base.update(kw)
        return base

    def test_estructura_completa(self):
        import conflux.app.install as inst
        with mock.patch.object(doctor, '_http', return_value=(200, {})), \
             mock.patch.object(doctor, '_port_free', return_value=False), \
             mock.patch.object(doctor, 'addon_state',
                               return_value={'state': 'registered'}), \
             mock.patch.object(doctor, 'collect_status', return_value=self._st(
                 running=True, cache_age_s=5,
                 sources={'total': 2, 'with_manifest': 1, 'degraded': []},
                 breakers_open=['h'])), \
             mock.patch.object(doctor.shutil, 'disk_usage',
                               return_value=mock.MagicMock(free=200 << 20)), \
             mock.patch.object(inst, 'is_installed', return_value=True):
            checks = dict((i, (lv, m)) for i, lv, m in doctor.collect_doctor())
        for cid in ('version', 'config', 'service', 'port', 'addon',
                    'authkey', 'sources', 'breakers', 'cache', 'disk',
                    'log', 'autostart'):
            self.assertIn(cid, checks)
        self.assertEqual(checks['service'][0], 'ok')
        self.assertEqual(checks['addon'][0], 'ok')
        self.assertEqual(checks['autostart'][0], 'ok')

    def test_servicio_caido_y_puerto_libre(self):
        with mock.patch.object(doctor, '_http', return_value=(None, None)), \
             mock.patch.object(doctor, '_port_free', return_value=True), \
             mock.patch.object(doctor, 'addon_state',
                               return_value={'state': 'failed',
                                             'last_error': 'boom'}), \
             mock.patch.object(doctor, 'collect_status',
                               return_value=self._st()), \
             mock.patch.object(doctor.shutil, 'disk_usage',
                               side_effect=OSError('x')), \
             mock.patch('conflux.app.install.is_installed',
                        side_effect=RuntimeError('x')):
            checks = dict((i, (lv, m)) for i, lv, m in doctor.collect_doctor())
        self.assertEqual(checks['service'][0], 'fail')
        self.assertEqual(checks['port'][0], 'warn')    # libre + caído = warn
        self.assertEqual(checks['addon'][0], 'fail')
        self.assertIn('boom', checks['addon'][1])
        self.assertEqual(checks['disk'][0], 'warn')
        self.assertEqual(checks['autostart'][0], 'warn')
        self.assertEqual(checks['cache'][0], 'warn')

    def test_authkey_excepcion_warn(self):
        with mock.patch.object(doctor, '_http', return_value=(200, {})), \
             mock.patch.object(doctor, '_port_free', return_value=False), \
             mock.patch.object(doctor, 'addon_state',
                               return_value={'state': 'registered'}), \
             mock.patch.object(doctor, 'collect_status',
                               return_value=self._st()), \
             mock.patch('conflux.infra.stremio.find_auth',
                        side_effect=RuntimeError('keyring roto')):
            checks = dict((i, (lv, m)) for i, lv, m in doctor.collect_doctor())
        self.assertEqual(checks['authkey'][0], 'warn')


class TestExitCodes(unittest.TestCase):
    def test_exit_authkey_fail_4(self):
        checks = [('authkey', 'fail', 'x')]
        with mock.patch.object(doctor, 'collect_doctor', return_value=checks):
            self.assertEqual(doctor.run_doctor(out=lambda *_: None), 4)

    def test_cli_diagnose_dispatch(self):
        with mock.patch.object(doctor, 'run_doctor', return_value=0) as rd:
            self.assertEqual(doctor.cli_diagnose(['doctor', '--json']), 0)
        rd.assert_called_once()
        with mock.patch.object(doctor, 'run_status', return_value=0) as rs:
            self.assertEqual(doctor.cli_diagnose(['status']), 0)
        rs.assert_called_once()


class TestAdminToken(unittest.TestCase):
    def test_ensure_generates_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'config.json')
            cfg_mod.CFG['admin_token'] = ''
            with mock.patch.object(cfg_mod, '_CFG_FILE', f):
                self.assertTrue(cfg_mod.ensure_admin_token())
                tok = cfg_mod.CFG['admin_token']
                self.assertGreaterEqual(len(tok), 40)   # 256 bits urlsafe
                self.assertTrue(os.path.exists(f))
                self.assertFalse(cfg_mod.ensure_admin_token())  # idempotente
                self.assertEqual(cfg_mod.CFG['admin_token'], tok)

    def test_rotate_invalidates(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'config.json')
            cfg_mod.CFG['admin_token'] = ''
            with mock.patch.object(cfg_mod, '_CFG_FILE', f):
                cfg_mod.ensure_admin_token()
                old = cfg_mod.CFG['admin_token']
                cfg_mod.rotate_admin_token()
                self.assertNotEqual(cfg_mod.CFG['admin_token'], old)


if __name__ == '__main__':
    unittest.main()
