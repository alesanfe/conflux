"""Tests de conflux.app.install — self-install del exe frozen (Windows).

winreg/subprocess/señales de frozen se inyectan: nada aquí toca el sistema
real (salvo ficheros en tmp_path).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), 'src'))

from conflux.app import install   # noqa: E402
from conflux.infra import stremio as _stremio   # noqa: E402


class _FakeKey:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 1
    REG_SZ = 1

    def __init__(self, values=None):
        self.store = dict(values or {})

    def OpenKey(self, root, path, _reserved=0, _access=0):
        if (root, path) != (self.HKEY_CURRENT_USER, install._RUN_KEY):
            raise OSError('key desconocida')
        return _FakeKey(self.store)

    def SetValueEx(self, key, name, _r, _t, value):
        key._store[name] = value

    def QueryValueEx(self, key, name):
        if name not in key._store:
            raise OSError('valor ausente')
        return key._store[name], self.REG_SZ

    def DeleteValue(self, key, name):
        if name not in key._store:
            raise OSError('valor ausente')
        del key._store[name]


def _frozen(tmp, monkey):
    """Simula un exe frozen en tmp/'bin'/conflux.exe con LOCALAPPDATA=tmp/'la'."""
    exe_dir = tmp / 'bin'
    exe_dir.mkdir(parents=True, exist_ok=True)
    exe = exe_dir / 'conflux.exe'
    exe.write_bytes(b'EXE')
    la = tmp / 'la'
    la.mkdir()
    appdata = tmp / 'roaming'
    appdata.mkdir()
    monkey.setenv('LOCALAPPDATA', str(la))
    monkey.setenv('APPDATA', str(appdata))
    monkey.setattr(sys, 'executable', str(exe))
    monkey.setattr(sys, 'frozen', True, raising=False)
    return exe, la, appdata


class _Monkey:
    """Mini-monkeypatch (sin pytest): setattr/setenv con undo automático."""

    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, val, raising=True):
        self._undo.append((obj, name, getattr(obj, name, None),
                           hasattr(obj, name)))
        setattr(obj, name, val)

    def setenv(self, name, val):
        self._undo.append((os.environ, name, os.environ.get(name), True))
        os.environ[name] = val

    def undo(self):
        for obj, name, old, existed in self._undo:
            if obj is os.environ:
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old
            elif existed:
                setattr(obj, name, old)
            else:
                delattr(obj, name)
        self._undo.clear()


class _TmpCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = _Path(self._td.name)
        self.mon = _Monkey()
        self.addCleanup(self.mon.undo)
        self.addCleanup(self._td.cleanup)


class _Path:
    """Path mínimo sobre strings (evita pathlib en tests ya portables)."""

    def __init__(self, s):
        self.s = s

    def __truediv__(self, other):
        return _Path(os.path.join(self.s, other))

    def mkdir(self, parents=False, exist_ok=False):
        os.makedirs(self.s, exist_ok=exist_ok)
        return self

    def write_bytes(self, b):
        with open(self.s, 'wb') as f:
            f.write(b)

    def write_text(self, t):
        with open(self.s, 'w', encoding='utf-8') as f:
            f.write(t)

    def exists(self):
        return os.path.exists(self.s)

    def __str__(self):
        return self.s


class TestNetstatPids(unittest.TestCase):
    def test_listening_en_puerto(self):
        text = ('  TCP    127.0.0.1:9700    0.0.0.0:0    LISTENING    4321\n'
                '  TCP    127.0.0.1:9700    127.0.0.1:5  ESTABLISHED  9999\n'
                '  TCP    0.0.0.0:97000     0.0.0.0:0    LISTENING    7777\n'
                '  UDP    0.0.0.0:9700      *:*                    5555\n')
        self.assertEqual(install._netstat_pids(text), [4321])


class TestIsInstalled(_TmpCase):
    def test_run_key_apunta_al_exe(self):
        exe, _la, _ad = _frozen(self.tmp, self.mon)
        wr = _FakeWinreg({'Conflux': f'"{exe}"'})
        self.mon.setattr(install, 'winreg', wr)
        self.assertTrue(install.is_installed())

    def test_run_key_a_otro_sitio(self):
        exe, _la, _ad = _frozen(self.tmp, self.mon)
        wr = _FakeWinreg({'Conflux': '"C:\\otro\\conflux.exe"'})
        self.mon.setattr(install, 'winreg', wr)
        self.assertFalse(install.is_installed())

    def test_sin_valor(self):
        _frozen(self.tmp, self.mon)
        self.mon.setattr(install, 'winreg', _FakeWinreg())
        self.assertFalse(install.is_installed())

    def test_sin_winreg(self):
        self.mon.setattr(install, 'winreg', None)
        self.assertFalse(install.is_installed())


class TestInstallSelf(_TmpCase):
    def _wire(self):
        exe, la, appdata = _frozen(self.tmp, self.mon)
        (self.tmp / 'bin' / 'config.json').write_text('{"a":1}')
        (self.tmp / 'bin' / 'sources.json').write_text('[]')
        startup = appdata.s + ('\\Microsoft\\Windows\\Start Menu'
                               '\\Programs\\Startup')
        os.makedirs(startup)
        with open(os.path.join(startup, 'Conflux.lnk'), 'w') as f:
            f.write('lnk')
        wr = _FakeWinreg()
        self.mon.setattr(install, 'winreg', wr)
        popen = mock.Mock()
        self.mon.setattr(install.subprocess, 'Popen', popen)
        reg = mock.Mock()
        self.mon.setattr(install, 'register_addon_bg', reg)
        return exe, la, startup, wr, popen, reg

    def test_primera_vez_copia_relanza_y_no_sirve(self):
        exe, la, startup, wr, popen, reg = self._wire()
        self.assertFalse(install.install_self())
        dest = os.path.join(str(la), 'Conflux')
        self.assertTrue(os.path.exists(os.path.join(dest, 'conflux.exe')))
        self.assertTrue(os.path.exists(os.path.join(dest, 'config.json')))
        self.assertTrue(os.path.exists(os.path.join(dest, 'sources.json')))
        self.assertEqual(wr.store.get('Conflux'),
                         f'"{os.path.join(dest, "conflux.exe")}"')
        self.assertFalse(os.path.exists(os.path.join(startup, 'Conflux.lnk')))
        popen.assert_called_once()
        reg.assert_not_called()          # el addon lo registra la copia lanzada

    def test_ya_en_destino_sirve_y_registra(self):
        exe, la, startup, wr, popen, reg = self._wire()
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        new_exe = os.path.join(dest, 'conflux.exe')
        with open(new_exe, 'wb') as f:
            f.write(b'EXE2')
        self.mon.setattr(sys, 'executable', new_exe)
        self.assertTrue(install.install_self())
        popen.assert_not_called()
        reg.assert_called_once_with(force=True)

    def test_upgrade_no_pisa_config(self):
        exe, la, startup, wr, popen, reg = self._wire()
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        with open(os.path.join(dest, 'config.json'), 'w') as f:
            f.write('{"vieja":true}')
        install.install_self()
        with open(os.path.join(dest, 'config.json')) as f:
            self.assertIn('vieja', f.read())


class TestUninstall(_TmpCase):
    def test_desmonta_todo(self):
        exe, la, appdata = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        for n in ('conflux.exe', 'config.json', 'addon.ok', 'conflux.log'):
            with open(os.path.join(dest, n), 'w') as f:
                f.write('x')
        self.mon.setattr(sys, 'executable', os.path.join(dest, 'conflux.exe'))
        wr = _FakeWinreg({'Conflux': '"x"'})
        self.mon.setattr(install, 'winreg', wr)
        self.mon.setattr(install, '_run', mock.Mock(
            return_value=mock.Mock(stdout=b'')))
        popen = mock.Mock()
        self.mon.setattr(install.subprocess, 'Popen', popen)
        stremio = mock.Mock()
        stremio.remove.return_value = ('removed', 'ok')
        with mock.patch.object(_stremio, 'remove', stremio.remove):
            install.uninstall(say=lambda *a, **k: None)
        self.assertEqual(wr.store, {})
        self.assertTrue(os.path.exists(os.path.join(dest, 'conflux.exe')))
        self.assertFalse(os.path.exists(os.path.join(dest, 'config.json')))
        self.assertFalse(os.path.exists(os.path.join(dest, 'addon.ok')))
        stremio.remove.assert_called_once()
        popen.assert_called_once()       # borrado diferido del propio exe


class TestCliEntry(_TmpCase):
    def test_dev_sin_frozen_siempre_sirve(self):
        self.mon.setattr(install, 'winreg', _FakeWinreg())
        self.assertTrue(install.cli_entry([]))
        self.assertTrue(install.cli_entry(['serve']))

    def test_uninstall_no_sirve(self):
        _frozen(self.tmp, self.mon)
        wr = _FakeWinreg()
        self.mon.setattr(install, 'winreg', wr)
        un = mock.Mock()
        self.mon.setattr(install, 'uninstall', un)
        self.assertFalse(install.cli_entry(['uninstall']))
        un.assert_called_once()

    def test_no_instalado_instala(self):
        _frozen(self.tmp, self.mon)
        self.mon.setattr(install, 'winreg', _FakeWinreg())
        ins = mock.Mock(return_value=False)
        self.mon.setattr(install, 'install_self', ins)
        self.assertFalse(install.cli_entry([]))
        ins.assert_called_once()

    def test_instalado_sirve_y_mira_marca(self):
        exe, _la, _ad = _frozen(self.tmp, self.mon)
        wr = _FakeWinreg({'Conflux': f'"{exe}"'})
        self.mon.setattr(install, 'winreg', wr)
        reg = mock.Mock()
        self.mon.setattr(install, 'register_addon_bg', reg)
        self.assertTrue(install.cli_entry([]))
        reg.assert_called_once_with()

    def test_addon_fuerza_registro(self):
        exe, _la, _ad = _frozen(self.tmp, self.mon)
        wr = _FakeWinreg({'Conflux': f'"{exe}"'})
        self.mon.setattr(install, 'winreg', wr)
        reg = mock.Mock()
        self.mon.setattr(install, 'register_addon_bg', reg)
        self.assertTrue(install.cli_entry(['addon']))
        reg.assert_called_once_with(force=True)


class TestRegisterAddon(_TmpCase):
    def test_marca_presente_salta(self):
        _exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        with open(os.path.join(dest, 'addon.ok'), 'w') as f:
            f.write('{}')
        with mock.patch.object(_stremio, 'install') as inst:
            install.register_addon()
        inst.assert_not_called()

    def test_exito_escribe_marca(self):
        _exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        with mock.patch.object(_stremio, 'install',
                               return_value=('installed', 'ok')), \
                mock.patch('urllib.request.urlopen'):
            install.register_addon()
        self.assertTrue(os.path.exists(os.path.join(dest, 'addon.ok')))


if __name__ == '__main__':
    unittest.main()
