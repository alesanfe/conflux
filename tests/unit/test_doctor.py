import io
import json
import os
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
