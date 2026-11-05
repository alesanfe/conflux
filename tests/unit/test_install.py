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
    monkey.setattr(sys, 'platform', 'win32')
    monkey.setattr(sys, 'executable', str(exe))
    monkey.setattr(sys, 'frozen', True, raising=False)
    return exe, la, appdata


def _frozen_posix(tmp, monkey, platform='linux'):
    """Simula un binario frozen en tmp/'bin'/conflux con HOME=tmp/'home'."""
    exe_dir = tmp / 'bin'
    exe_dir.mkdir(parents=True, exist_ok=True)
    exe = exe_dir / 'conflux'
    exe.write_bytes(b'ELF')
    home = tmp / 'home'
    home.mkdir()
    monkey.setenv('HOME', str(home))
    monkey.setenv('USERPROFILE', str(home))      # expanduser en Windows-host
    monkey.setenv('XDG_DATA_HOME', os.path.join(str(home), '.xdg'))
    monkey.setenv('XDG_CONFIG_HOME', os.path.join(str(home), '.xcfg'))
    monkey.setattr(sys, 'platform', platform)
    monkey.setattr(sys, 'executable', str(exe))
    monkey.setattr(sys, 'frozen', True, raising=False)
    return exe, home


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


class TestPosixPaths(_TmpCase):
    def test_dest_dir_linux_xdg(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        self.assertTrue(install._dest_dir().endswith(
            os.path.join('.xdg', 'conflux')))
        self.assertTrue(install._dest_exe().endswith(
            os.path.join('conflux', 'conflux')))

    def test_dest_dir_darwin(self):
        _frozen_posix(self.tmp, self.mon, 'darwin')
        self.assertIn(os.path.join('Library', 'Application Support',
                                   'Conflux'), install._dest_dir())


class TestPosixAutostart(_TmpCase):
    def test_linux_systemd_unit(self):
        exe, home = _frozen_posix(self.tmp, self.mon, 'linux')
        runs = []
        self.mon.setattr(install, '_run',
                         lambda *a, **k: runs.append(a[0]))
        self.mon.setattr(install.shutil, 'which', lambda c: '/usr/bin/systemctl')
        dest = os.path.join(str(home), '.xdg', 'conflux', 'conflux')
        install._set_autostart(dest)
        unit = os.path.join(str(home), '.xcfg', 'systemd', 'user',
                            'conflux.service')
        with open(unit) as f:
            body = f.read()
        self.assertIn(f'ExecStart={dest} serve', body)
        self.assertIn(['systemctl', '--user', 'enable',
                       'conflux.service'], runs)
        self.assertTrue(install.is_installed())

    def test_linux_sin_systemd_desktop(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        home = self.tmp / 'home'
        self.mon.setattr(install.shutil, 'which', lambda c: None)
        install._set_autostart('/x/conflux')
        desk = os.path.join(str(home), '.xcfg', 'autostart',
                            'conflux.desktop')
        with open(desk) as f:
            self.assertIn('Exec=/x/conflux serve', f.read())
        self.assertTrue(install.is_installed())

    def test_darwin_plist_y_launchctl(self):
        _frozen_posix(self.tmp, self.mon, 'darwin')
        home = self.tmp / 'home'
        runs = []
        self.mon.setattr(install, '_run',
                         lambda *a, **k: runs.append(a[0]))
        install._set_autostart('/x/conflux')
        plist = os.path.join(str(home), 'Library', 'LaunchAgents',
                             'site.conflux.plist')
        with open(plist) as f:
            body = f.read()
        self.assertIn('<string>/x/conflux</string>', body)
        self.assertIn('<key>RunAtLoad</key>', body)
        self.assertIn(['launchctl', 'load', '-w', plist], runs)
        self.assertTrue(install.is_installed())

    def test_clear_autostart_linux(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        home = self.tmp / 'home'
        runs = []
        self.mon.setattr(install, '_run',
                         lambda *a, **k: runs.append(a[0]))
        self.mon.setattr(install.shutil, 'which', lambda c: '/usr/bin/systemctl')
        install._set_autostart('/x/conflux')
        install._clear_autostart()
        unit = os.path.join(str(home), '.xcfg', 'systemd', 'user',
                            'conflux.service')
        self.assertFalse(os.path.exists(unit))
        self.assertIn(['systemctl', '--user', 'disable', '--now',
                       'conflux.service'], runs)
        self.assertFalse(install.is_installed())


class TestPosixKill(_TmpCase):
    def test_kill_sigterm_y_propio_pid_excluido(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        calls = []
        self.mon.setattr(install, '_posix_pids',
                         lambda say: {os.getpid(), 4242})
        self.mon.setattr(install.os, 'kill',
                         lambda pid, sig: calls.append((pid, sig)))
        n = install.kill_proxy(say=lambda *a, **k: None)
        self.assertEqual(n, 1)
        self.assertEqual(calls[0][0], 4242)
        self.assertEqual(calls[0][1], install.signal.SIGTERM)
        self.assertNotIn((os.getpid(), install.signal.SIGTERM), calls)

    def test_posix_pids_pidfile(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        dest = install._dest_dir()
        os.makedirs(dest)
        with open(os.path.join(dest, 'conflux.pid'), 'w') as f:
            f.write('4321')
        self.mon.setattr(install, '_proc_pids', lambda: set())
        self.assertEqual(install._posix_pids(lambda *a: None), {4321})

    def test_posix_pids_lsof_darwin(self):
        _frozen_posix(self.tmp, self.mon, 'darwin')
        self.mon.setattr(install, '_run', lambda *a, **k:
                         mock.Mock(stdout=b'777\n'))
        self.assertEqual(install._posix_pids(lambda *a: None), {777})


class TestPosixInstallUninstall(_TmpCase):
    def test_install_copia_chmod_y_relanza(self):
        exe, home = _frozen_posix(self.tmp, self.mon, 'linux')
        runs = []
        self.mon.setattr(install, '_run',
                         lambda *a, **k: runs.append(a[0]))
        self.mon.setattr(install.shutil, 'which', lambda c: None)
        spawn = mock.Mock()
        self.mon.setattr(install, '_spawn', spawn)
        self.assertFalse(install.install_self())
        dest = os.path.join(str(home), '.xdg', 'conflux', 'conflux')
        self.assertTrue(os.path.exists(dest))
        if os.name == 'posix':               # st_mode real solo en host posix
            self.assertEqual(os.stat(dest).st_mode & 0o111, 0o111)
        spawn.assert_called_once_with([dest])

    def test_uninstall_borra_dir_entero(self):
        exe, home = _frozen_posix(self.tmp, self.mon, 'linux')
        dest = install._dest_dir()
        os.makedirs(dest)
        for n in ('conflux', 'config.json', 'addon.ok', 'conflux.pid'):
            with open(os.path.join(dest, n), 'w') as f:
                f.write('x')
        self.mon.setattr(sys, 'executable', os.path.join(dest, 'conflux'))
        self.mon.setattr(install, '_posix_pids', lambda say: set())
        self.mon.setattr(install.shutil, 'which', lambda c: None)
        stremio = mock.Mock()
        stremio.remove.return_value = ('removed', 'ok')
        with mock.patch.object(_stremio, 'remove', stremio.remove):
            install.uninstall(say=lambda *a, **k: None)
        self.assertFalse(os.path.exists(dest))   # incluido el propio binario
        stremio.remove.assert_called_once()

    def test_cli_linux_instala_si_falta_unit(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        ins = mock.Mock(return_value=False)
        self.mon.setattr(install, 'install_self', ins)
        self.assertFalse(install.cli_entry([]))
        ins.assert_called_once()


if __name__ == '__main__':
    unittest.main()
