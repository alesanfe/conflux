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
        # LIFO: un doble patch del mismo attr debe restaurar el original,
        # no el valor intermedio (test_manual_deeplink_posix parchea
        # _spawn dos veces — undo FIFO dejaba _spawn=lambda muerta).
        for obj, name, old, existed in reversed(self._undo):
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
        lnk = install._lnk_path()             # la ruta real que mira el código
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        with open(lnk, 'w') as f:
            f.write('lnk')
        wr = _FakeWinreg()
        self.mon.setattr(install, 'winreg', wr)
        popen = mock.Mock()
        self.mon.setattr(install.subprocess, 'Popen', popen)
        reg = mock.Mock()
        self.mon.setattr(install, 'register_addon_bg', reg)
        return exe, la, lnk, wr, popen, reg

    def test_primera_vez_copia_relanza_y_no_sirve(self):
        exe, la, lnk, wr, popen, reg = self._wire()
        self.assertFalse(install.install_self())
        dest = os.path.join(str(la), 'Conflux')
        self.assertTrue(os.path.exists(os.path.join(dest, 'conflux.exe')))
        self.assertTrue(os.path.exists(os.path.join(dest, 'config.json')))
        self.assertTrue(os.path.exists(os.path.join(dest, 'sources.json')))
        self.assertEqual(wr.store.get('Conflux'),
                         f'"{os.path.join(dest, "conflux.exe")}"')
        self.assertFalse(os.path.exists(lnk))
        popen.assert_called_once()
        reg.assert_not_called()          # el addon lo registra la copia lanzada

    def test_ya_en_destino_sirve_y_registra(self):
        exe, la, lnk, wr, popen, reg = self._wire()
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
        exe, la, lnk, wr, popen, reg = self._wire()
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

    def test_uninstall_idempotente_segunda_vez(self):
        """uninstall sobre un sistema ya desinstalado: no-op limpio (CONFLUX-046)."""
        exe, la, appdata = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')     # nada instalado
        wr = _FakeWinreg()
        self.mon.setattr(install, 'winreg', wr)
        self.mon.setattr(install, '_run', mock.Mock(
            return_value=mock.Mock(stdout=b'')))
        popen = mock.Mock()
        self.mon.setattr(install.subprocess, 'Popen', popen)
        with mock.patch.object(_stremio, 'remove',
                               mock.Mock(return_value=('x', 'ok'))):
            install.uninstall(say=lambda *a, **k: None)   # no lanza
        self.assertFalse(os.path.exists(dest))

    def test_install_dos_veces_conserva_config(self):
        """re-instalar encima (upgrade) nunca pisa la config del usuario."""
        exe, la, appdata = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        cfg_p = os.path.join(dest, 'config.json')
        with open(cfg_p, 'w') as f:
            f.write('{"admin_token":"keep-me"}')
        self.mon.setattr(install, 'winreg', _FakeWinreg())
        self.mon.setattr(install, '_run', mock.Mock(
            return_value=mock.Mock(stdout=b'')))
        self.mon.setattr(install.subprocess, 'Popen', mock.Mock())
        self.mon.setattr(install, 'register_addon_bg', mock.Mock())
        for _ in range(2):                              # install×2 = mismo resultado
            install.install_self()
        with open(cfg_p) as f:
            self.assertIn('keep-me', f.read())


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


class TestSpawnRun(_TmpCase):
    def test_spawn_posix_nueva_sesion(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        popen = mock.Mock()
        self.mon.setattr(install.subprocess, 'Popen', popen)
        install._spawn(['/x/conflux'])
        self.assertTrue(popen.call_args[1].get('start_new_session'))

    def test_run_ejecuta_subprocess(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        fake = mock.Mock(returncode=0)
        self.mon.setattr(install.subprocess, 'run',
                         mock.Mock(return_value=fake))
        r = install._run(['whatever'], timeout=7)
        self.assertIs(r, fake)
        kw = install.subprocess.run.call_args[1]
        self.assertTrue(kw['capture_output'])
        self.assertEqual(kw['timeout'], 7)
        self.assertFalse(kw['check'])


class TestProcPids(_TmpCase):
    """_proc_pids: /proc/net/tcp parse + /proc/<pid>/fd readlink."""

    def _fake_proc(self, monkey):
        import builtins
        tcp = ('  sl  local_address rem_address   st tx_queue rx_queue '
               'tr tm->when retrnsmt   uid  timeout inode\n'
               '   0: 0100007F:2588 00000000:0000 0A 00:00 00:00 00 0 '
               '0 12345\n')
        real_open = open

        def fopen(p, *a, **k):
            if p in ('/proc/net/tcp', '/proc/net/tcp6'):
                import io
                return io.StringIO(tcp)
            return real_open(p, *a, **k)

        monkey.setattr(builtins, 'open', fopen)
        monkey.setattr(install.os, 'listdir', lambda p:
                       ['999'] if p == '/proc' else ['3'])
        monkey.setattr(install.os, 'readlink',
                       lambda p: 'socket:[12345]')

    def test_encuentra_pid_por_socket(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        self._fake_proc(self.mon)
        self.assertEqual(install._proc_pids(0x2588), {999})

    def test_tab_oserror_sin_inodes(self):
        import builtins
        _frozen_posix(self.tmp, self.mon, 'linux')
        self.mon.setattr(builtins, 'open',
                         mock.Mock(side_effect=OSError('no proc')))
        self.assertEqual(install._proc_pids(), set())

    def test_lineas_no_listen_ignoradas(self):
        """Filas cortas/estado!=0A/puerto distinto no cuentan (141->139)."""
        import builtins
        _frozen_posix(self.tmp, self.mon, 'linux')
        tcp = ('  sl  local_address rem_address   st\n'
               '   0: 0100007F:2588 00000000:0000 01 x\n'   # st=01 no listen
               '   1: 0100007F:9999 00000000:0000 0A x\n'   # puerto != 0x2588
               '   2: 0100007F:2588 00000000:0000 0A x x x x x x x 999\n')
        real_open = open
        def fopen(p, *a, **k):
            if 'tcp' in p:
                import io
                return io.StringIO(tcp)
            return real_open(p, *a, **k)
        self.mon.setattr(builtins, 'open', fopen)
        self.mon.setattr(install.os, 'listdir', lambda p: [] if p == '/proc'
                         else [])
        self.assertEqual(install._proc_pids(0x2588), set())   # inode 999 sin pid

    def test_fd_listdir_oserror_continua(self):
        """Un /proc/<pid>/fd ilegible no tumba el escaneo (150)."""
        import builtins
        _frozen_posix(self.tmp, self.mon, 'linux')
        tcp = ('  sl  local_address rem_address   st x x x x x x x inode\n'
               '   0: 0100007F:2588 00000000:0000 0A x x x x x x x 555\n')
        real_open = open
        def fopen(p, *a, **k):
            if 'tcp' in p:
                import io
                return io.StringIO(tcp)
            return real_open(p, *a, **k)
        self.mon.setattr(builtins, 'open', fopen)
        self.mon.setattr(install.os, 'listdir', mock.Mock(
            side_effect=lambda p: ['1', 'abc'] if p == '/proc'
            else (_ for _ in ()).throw(OSError('perm'))))
        self.assertEqual(install._proc_pids(0x2588), set())

    def test_fd_readlink_oserror_y_no_socket(self):
        """fds no-socket o readlink roto se saltan (153->152, 157-158)."""
        import builtins
        _frozen_posix(self.tmp, self.mon, 'linux')
        tcp = ('  sl  local_address rem_address   st x x x x x x x inode\n'
               '   0: 0100007F:2588 00000000:0000 0A x x x x x x x 555\n')
        real_open = open
        def fopen(p, *a, **k):
            if 'tcp' in p:
                import io
                return io.StringIO(tcp)
            return real_open(p, *a, **k)
        self.mon.setattr(builtins, 'open', fopen)
        self.mon.setattr(install.os, 'listdir',
                         lambda p: ['7'] if p == '/proc' else ['0', '1'])
        def rl(p):
            if p.endswith('/fd/0'):
                return 'pipe:[99]'                    # no socket -> skip
            raise OSError('fd cerrado')
        self.mon.setattr(install.os, 'readlink', rl)
        self.assertEqual(install._proc_pids(0x2588), set())


class TestPosixKillEdge(_TmpCase):
    def test_lsof_falla_say(self):
        _frozen_posix(self.tmp, self.mon, 'darwin')
        self.mon.setattr(install, '_run', mock.Mock(
            side_effect=OSError('no lsof')))
        says = []
        self.assertEqual(install._posix_pids(
            lambda *a, **k: says.append(a)), set())
        self.assertTrue(any('lsof' in str(s) for s in says))

    def test_sigkill_oserror_tragado(self):
        self.mon.setattr(install.os, 'kill',
                         mock.Mock(side_effect=OSError('gone')))
        install._sigkill(1234)                  # no propaga

    def test_kill_sigterm_oserror_continua(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        self.mon.setattr(install, '_posix_pids', lambda say: {777})
        calls = []
        def fake_kill(pid, sig):
            calls.append(sig)
            raise OSError('gone')
        self.mon.setattr(install.os, 'kill', fake_kill)
        self.mon.setattr(install.time, 'sleep', lambda s: None)
        n = install.kill_proxy(say=lambda *a, **k: None)
        self.assertEqual(n, 1)

    def test_kill_sigterm_insuficiente_escala_sigkill(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        self.mon.setattr(install, '_posix_pids', lambda say: {777})
        calls = []
        self.mon.setattr(install.os, 'kill',
                         lambda pid, sig: calls.append(sig))
        self.mon.setattr(install.time, 'sleep', lambda s: None)
        install.kill_proxy(say=lambda *a, **k: None)
        self.assertIn(install.signal.SIGTERM, calls)
        self.assertTrue(any(s != install.signal.SIGTERM for s in calls))


@unittest.skipUnless(sys.platform == 'win32', 'kill_proxy vía netstat/taskkill')
class TestWinKillEdge(_TmpCase):
    def test_netstat_falla_say_y_taskkill_im(self):
        exe, _la, _ad = _frozen(self.tmp, self.mon)
        runs = []
        def fake_run(cmd, timeout=15):
            runs.append(cmd)
            if 'netstat' in cmd[0] if isinstance(cmd, list) else 'netstat' in cmd:
                raise OSError('sin netstat')
            return mock.Mock(stdout=b'')
        self.mon.setattr(install, '_run', fake_run)
        says = []
        n = install.kill_proxy(say=lambda *a, **k: says.append(a))
        self.assertTrue(any('netstat' in str(s) for s in says))
        self.assertTrue(any('taskkill' in str(c) for c in runs))
        self.assertEqual(n, 0)

    def test_taskkill_por_pid_encontrado(self):
        exe, _la, _ad = _frozen(self.tmp, self.mon)
        netstat = ('  TCP    127.0.0.1:9700    0.0.0.0:0    LISTENING'
                   '       4242\r\n')
        runs = []
        def fake_run(cmd, timeout=15):
            c = cmd if isinstance(cmd, list) else [cmd]
            runs.append(c)
            return mock.Mock(stdout=netstat.encode())
        self.mon.setattr(install, '_run', fake_run)
        install.kill_proxy(say=lambda *a, **k: None)
        self.assertTrue(any('taskkill' in c and '4242' in c for c in runs))


class TestSetClearAutostartWin(_TmpCase):
    def test_set_sin_winreg_noop(self):
        _frozen(self.tmp, self.mon)
        self.mon.setattr(install, 'winreg', None)
        install._set_autostart('/x/conflux.exe')   # no-op limpio

    def test_set_escribe_run_key(self):
        _frozen(self.tmp, self.mon)
        wr = _FakeWinreg()
        self.mon.setattr(install, 'winreg', wr)
        install._set_autostart('C:\\x\\conflux.exe')
        self.assertIn('Conflux', wr.store)

    def test_clear_borra_valor(self):
        _frozen(self.tmp, self.mon)
        wr = _FakeWinreg({'Conflux': '"C:\\x\\conflux.exe"'})
        self.mon.setattr(install, 'winreg', wr)
        install._clear_autostart()
        self.assertNotIn('Conflux', wr.store)

    def test_clear_sin_winreg_noop(self):
        _frozen(self.tmp, self.mon)
        self.mon.setattr(install, 'winreg', None)
        install._clear_autostart()


class TestClearAutostartDarwin(_TmpCase):
    def test_unload_y_unlink(self):
        _frozen_posix(self.tmp, self.mon, 'darwin')
        home = self.tmp / 'home'
        plist = os.path.join(str(home), 'Library', 'LaunchAgents',
                             'site.conflux.plist')
        os.makedirs(os.path.dirname(plist))
        with open(plist, 'w') as f:
            f.write('x')
        runs = []
        self.mon.setattr(install, '_run',
                         lambda *a, **k: runs.append(a[0]))
        install._clear_autostart()
        self.assertFalse(os.path.exists(plist))
        self.assertIn(['launchctl', 'unload', '-w', plist], runs)


class TestWritePidEdge(_TmpCase):
    def test_makedirs_oserror_tragado(self):
        _frozen_posix(self.tmp, self.mon, 'linux')
        self.mon.setattr(install.os, 'makedirs',
                         mock.Mock(side_effect=OSError('ro')))
        install._write_pid()                     # no propaga


class TestRegisterAddonEdge(_TmpCase):
    def test_proxy_nunca_responde_remote_unreachable(self):
        _exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        self.mon.setattr(install.time, 'sleep', lambda s: None)
        states = []
        with mock.patch('urllib.request.urlopen',
                        side_effect=OSError('down')), \
             mock.patch('conflux.app.doctor.set_addon_state',
                        side_effect=lambda s, **k: states.append(s)):
            install.register_addon()
        self.assertIn('remote_unreachable', states)

    def test_manual_deeplink_win(self):
        _exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        opened = []
        self.mon.setattr(install.os, 'startfile',
                         lambda p: opened.append(p), raising=False)
        states = []
        with mock.patch('urllib.request.urlopen', return_value=mock.Mock()), \
             mock.patch.object(_stremio, 'install',
                               return_value=('manual', 'sin key')), \
             mock.patch('conflux.app.doctor.set_addon_state',
                        side_effect=lambda s, **k: states.append(s)):
            install.register_addon()
        self.assertIn('authentication_failed', states)
        self.assertTrue(any('stremio://' in p for p in opened))

    def test_manual_deeplink_posix(self):
        """manual en linux -> xdg-open; en darwin -> open (376-381)."""
        for plat, cmd in (('linux', 'xdg-open'), ('darwin', 'open')):
            with self.subTest(plat=plat):
                td = __import__('tempfile').TemporaryDirectory()
                self.addCleanup(td.cleanup)
                self.tmp = _Path(td.name)
                _frozen_posix(self.tmp, self.mon, plat)
                dest = install._dest_dir()
                os.makedirs(dest)
                spawned = []
                self.mon.setattr(install, '_spawn',
                                 lambda a: spawned.append(a))
                with mock.patch('urllib.request.urlopen',
                                return_value=mock.Mock()), \
                     mock.patch.object(_stremio, 'install',
                                       return_value=('manual', 'k')):
                    install.register_addon()
                self.assertEqual(spawned[0][0], cmd)
                self.assertIn('stremio://', spawned[0][1])

    def test_manual_deeplink_oserror_tragado(self):
        _exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        self.mon.setattr(install.os, 'startfile',
                         mock.Mock(side_effect=OSError('no handler')),
                         raising=False)
        with mock.patch('urllib.request.urlopen',
                        return_value=mock.Mock()), \
             mock.patch.object(_stremio, 'install',
                               return_value=('manual', 'k')):
            install.register_addon()           # no propaga

    def test_health_retry_segundo_intento(self):
        """Primer urlopen falla, segundo ok -> sigue (356->354)."""
        _exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        calls = []
        def flaky(url, timeout=None):
            calls.append(url)
            if len(calls) == 1:
                raise OSError('aún no')
            return mock.Mock()
        self.mon.setattr(install.time, 'sleep', lambda s: None)
        with mock.patch('urllib.request.urlopen', side_effect=flaky), \
             mock.patch.object(_stremio, 'install',
                               return_value=('installed', 'ok')):
            install.register_addon()
        self.assertGreaterEqual(len(calls), 2)

    def test_error_retry_pending(self):
        _exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        states = []
        with mock.patch('urllib.request.urlopen', return_value=mock.Mock()), \
             mock.patch.object(_stremio, 'install',
                               return_value=('error', 'boom')), \
             mock.patch('conflux.app.doctor.set_addon_state',
                        side_effect=lambda s, **k: states.append(s)):
            install.register_addon()
        self.assertIn('retry_pending', states)

    def test_bg_lanza_hilo_daemon(self):
        started = []
        class _T:
            def __init__(self, target=None, args=None, daemon=None, name=None):
                started.append((daemon, name))
            def start(self):
                pass
        self.mon.setattr(install.threading, 'Thread', _T)
        install.register_addon_bg(force=True)
        self.assertEqual(started, [(True, 'addon-register')])


class TestInstallSelfEdge(_TmpCase):
    def test_destino_inescribible_sirve_en_sitio(self):
        _frozen(self.tmp, self.mon)
        self.mon.setattr(install.os, 'makedirs',
                         mock.Mock(side_effect=OSError('denied')))
        warns = []
        self.mon.setattr(install._log, 'warning',
                         lambda *a, **k: warns.append(a))
        self.assertTrue(install.install_self())
        self.assertTrue(warns)

    def test_config_copy_oserror_se_ignora(self):
        exe, la, _ad = _frozen(self.tmp, self.mon)
        # config.json junto al exe, copy2 revienta -> no aborta el install
        bin_dir = os.path.dirname(str(exe))
        with open(os.path.join(bin_dir, 'config.json'), 'w') as f:
            f.write('{}')
        self.mon.setattr(install, 'kill_proxy', lambda: 0)
        self.mon.setattr(install, '_set_autostart', lambda x: None)
        self.mon.setattr(install, '_spawn', mock.Mock())
        real_copy2 = install.shutil.copy2
        def picky(src, dst):
            if str(src).endswith('.json'):
                raise OSError('ro')
            return real_copy2(src, dst)
        self.mon.setattr(install.shutil, 'copy2', picky)
        install.install_self()                   # llega al final sin crash

    def test_autostart_oserror_sigue_sirviendo(self):
        exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux', 'conflux.exe')
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self.mon.setattr(sys, 'executable', dest)     # ya instalado
        self.mon.setattr(install, '_set_autostart',
                         mock.Mock(side_effect=OSError('registro ro')))
        warns = []
        self.mon.setattr(install._log, 'warning',
                         lambda *a, **k: warns.append(a))
        self.mon.setattr(install, 'register_addon_bg', mock.Mock())
        self.assertTrue(install.install_self())
        self.assertTrue(any('autostart' in str(w) for w in warns))


class TestUninstallEdge(_TmpCase):
    def test_stremio_remove_explota_continua(self):
        exe, la, _ad = _frozen(self.tmp, self.mon)
        dest = os.path.join(str(la), 'Conflux')
        os.makedirs(dest)
        self.mon.setattr(install, 'kill_proxy', lambda say: 0)
        self.mon.setattr(install, '_clear_autostart', lambda: None)
        self.mon.setattr(install, '_clear_lnk', lambda: None)
        with mock.patch.object(_stremio, 'remove',
                               side_effect=RuntimeError('boom')):
            install.uninstall(say=lambda *a, **k: None)


class TestCliEntryEdge(_TmpCase):
    def test_log_level_valido(self):
        _frozen(self.tmp, self.mon)
        self.mon.setattr(install, 'is_installed', lambda: True)
        self.mon.setattr(install, 'register_addon_bg', mock.Mock())
        self.mon.setattr(install, '_write_pid', lambda: None)
        install.cli_entry(['--log-level=DEBUG', 'serve'])

    def test_log_level_invalido_warn(self):
        _frozen(self.tmp, self.mon)
        warns = []
        self.mon.setattr(install._log, 'warning',
                         lambda *a, **k: warns.append(a))
        self.mon.setattr(install, 'is_installed', lambda: True)
        self.mon.setattr(install, 'register_addon_bg', mock.Mock())
        self.mon.setattr(install, '_write_pid', lambda: None)
        install.cli_entry(['--log-level=NIVEL_RARO', 'serve'])
        self.assertTrue(warns)

    def test_rotate_admin_token_salida(self):
        _frozen(self.tmp, self.mon)
        import conflux.core.cfg as cfgmod
        self.mon.setattr(cfgmod, 'rotate_admin_token', mock.Mock())
        self.assertFalse(install.cli_entry(['rotate-admin-token']))

    def test_doctor_systemexit(self):
        _frozen(self.tmp, self.mon)
        import conflux.app.doctor as doc
        self.mon.setattr(doc, 'cli_diagnose',
                         lambda argv, out=print: 0)
        with self.assertRaises(SystemExit):
            install.cli_entry(['doctor'])

    def test_install_arg_install_self_false(self):
        exe, la, _ad = _frozen(self.tmp, self.mon)
        wr = _FakeWinreg({'Conflux': f'"{exe}"'})
        self.mon.setattr(install, 'winreg', wr)
        ins = mock.Mock(return_value=False)
        self.mon.setattr(install, 'install_self', ins)
        self.assertFalse(install.cli_entry(['install']))

    def test_install_arg_install_self_true_sirve(self):
        """'install' con install_self True -> _write_pid + sirve (501->505)."""
        exe, la, _ad = _frozen(self.tmp, self.mon)
        wr = _FakeWinreg({'Conflux': f'"{exe}"'})
        self.mon.setattr(install, 'winreg', wr)
        self.mon.setattr(install, 'install_self',
                         mock.Mock(return_value=True))
        pid = mock.Mock()
        self.mon.setattr(install, '_write_pid', pid)
        self.assertTrue(install.cli_entry(['install']))
        pid.assert_called_once()


if __name__ == '__main__':
    unittest.main()
