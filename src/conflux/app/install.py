"""Self-install del binario frozen: `conflux[.exe]` solo monta el entorno.

Flujo:
- primer arranque (o `conflux install`): se copia al directorio de datos de la
  plataforma (%LOCALAPPDATA%\\Conflux en Windows, ~/Library/Application
  Support/Conflux en macOS, ${XDG_DATA_HOME:-~/.local/share}/conflux en Linux),
  registra autostart (Run key HKCU / LaunchAgent / systemd user unit, con
  fallback a XDG autostart si no hay systemd), elimina el Conflux.lnk legado y
  relanza la copia instalada; el registro del addon en Stremio corre en un hilo
  tras /health (marca `addon.ok` para no reinstalar un addon que el usuario
  quitó a mano).
- `conflux uninstall`: mata :9700, quita autostart, desregistra el addon remoto
  y borra el directorio (incluido el propio binario; borrado diferido en
  Windows porque el exe en uso no puede eliminarse).
- `conflux addon`: re-registra el addon (si el usuario lo quitó y quiere
  recuperarlo).
- `conflux serve` / sin args ya instalado: solo sirve.

Sin frozen (dev `python proxy.py`) es no-op.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import importlib

# linux/mac: winreg no existe — el módulo entero es no-op (cli_entry lo guarda)
winreg = (importlib.import_module('winreg')
          if sys.platform == 'win32' else None)

from conflux.core import state                     # noqa: E402
from conflux.core.state import _log, _atomic_json   # noqa: E402

PROXY = 'http://127.0.0.1:9700'
_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_VALUE = 'Conflux'
_NO_WIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_DETACHED = _NO_WIN | getattr(subprocess, 'DETACHED_PROCESS', 0) \
    | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)


def _lnk_path():
    return os.path.join(os.environ.get('APPDATA', ''),
                        'Microsoft\\Windows\\Start Menu\\Programs\\Startup',
                        'Conflux.lnk')


def _xdg(var, default):
    return os.environ.get(var) or default


def _dest_dir():
    home = os.path.expanduser('~')
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('LOCALAPPDATA') or home, 'Conflux')
    if sys.platform == 'darwin':
        return os.path.join(home, 'Library', 'Application Support', 'Conflux')
    xdg = _xdg('XDG_DATA_HOME', os.path.join(home, '.local', 'share'))
    return os.path.join(xdg, 'conflux')


def _dest_exe():
    name = 'conflux.exe' if sys.platform == 'win32' else 'conflux'
    return os.path.join(_dest_dir(), name)


def _xdg_cfg(*parts):
    cfg = _xdg('XDG_CONFIG_HOME', os.path.join(os.path.expanduser('~'),
                                             '.config'))
    return os.path.join(cfg, *parts)


def _unit_path():          # systemd user unit (linux)
    return _xdg_cfg('systemd', 'user', 'conflux.service')


def _desktop_path():       # fallback XDG autostart (linux sin systemd)
    return _xdg_cfg('autostart', 'conflux.desktop')


def _plist_path():         # LaunchAgent (macOS)
    return os.path.join(os.path.expanduser('~'), 'Library', 'LaunchAgents',
                        'site.conflux.plist')


def _pidfile():
    return os.path.join(_dest_dir(), 'conflux.pid')


def _spawn(argv):
    """Lanza desacoplado: flags de consola en win, sesión nueva en posix."""
    if sys.platform == 'win32':
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                creationflags=_DETACHED)
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True,
                            start_new_session=True)


def _norm(p):
    return os.path.normcase(os.path.realpath(p))


def _run(cmd, timeout=15):
    return subprocess.run(cmd, capture_output=True, timeout=timeout,
                          creationflags=_NO_WIN, check=False)


def _netstat_pids(text, port=9700):
    """PIDs LISTENING en el puerto, según la salida de `netstat -ano`."""
    out = []
    for ln in text.splitlines():
        cols = ln.split()
        if len(cols) >= 5 and cols[0].upper().startswith('TCP') \
                and cols[1].rsplit(':', 1)[-1] == str(port) \
                and cols[3].upper() == 'LISTENING' \
                and cols[-1].isdigit():
            out.append(int(cols[-1]))
    return out


def _proc_pids(port=9700):
    """PIDs dueños del socket :port leyendo /proc (linux, sin lsof/netstat)."""
    inodes = set()
    want = f'{port:04X}'
    for tab in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(tab, encoding='ascii') as fh:
                next(fh)
                for ln in fh:
                    cols = ln.split()
                    if len(cols) > 9 and cols[3] == '0A' \
                            and cols[1].rsplit(':', 1)[-1] == want:
                        inodes.add(cols[9])
        except OSError:
            pass
    pids = set()
    if inodes:
        for pid in os.listdir('/proc'):
            if not pid.isdigit():
                continue
            try:
                for fd in os.listdir(f'/proc/{pid}/fd'):
                    if os.readlink(f'/proc/{pid}/fd/{fd}') \
                            .removeprefix('socket:[').removesuffix(']') \
                            in inodes:
                        pids.add(int(pid))
            except OSError:
                continue
    return pids


def _posix_pids(say):
    """PIDs escuchando :9700 en posix: pidfile propio + /proc|lsof."""
    pids = set()
    try:
        with open(_pidfile(), encoding='ascii') as fh:
            pids.add(int(fh.read().strip()))
    except (OSError, ValueError):
        pass
    if sys.platform.startswith('linux'):
        pids.update(_proc_pids())
    else:                                                    # darwin: lsof
        try:
            res = _run(['lsof', '-ti', 'tcp:9700', '-sTCP:LISTEN'])
            out = res.stdout.decode() if isinstance(res.stdout, bytes) \
                else res.stdout
            pids.update(int(x) for x in out.split() if x.isdigit())
        except Exception as e:                               # noqa: BLE001
            say('install: lsof falló (%s)', e)
    return pids


def _sigkill(pid):
    try:
        os.kill(pid, getattr(signal, 'SIGKILL', signal.SIGTERM))
    except OSError:
        pass


def kill_proxy(say=_log.info):
    """Mata lo que escuche en :9700 y otras copias de conflux (excepto esta)."""
    own = os.getpid()
    if sys.platform != 'win32':
        killed = _posix_pids(say) - {own}
        for pid in killed:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(.3)
        for pid in killed:                                   # sigterm no bastó
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            _sigkill(pid)
        return len(killed)
    killed = set()
    try:
        res = _run('netstat -ano')
        out = res.stdout.decode('mbcs', 'replace') \
            if isinstance(res.stdout, bytes) else res.stdout
        killed.update(_netstat_pids(out))
    except Exception as e:                                     # noqa: BLE001
        say('install: netstat falló (%s)', e)
    for pid in killed - {own}:
        _run(['taskkill', '/PID', str(pid), '/F'])
    _run(['taskkill', '/IM', 'conflux.exe', '/F',
          '/FI', f'PID ne {own}'])
    return len(killed - {own})


_UNIT = """[Unit]
Description=Conflux — proxy agregador de addons Stremio

[Service]
ExecStart={exe} serve
Restart=on-failure

[Install]
WantedBy=default.target
"""

_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>site.conflux</string>
  <key>ProgramArguments</key><array><string>{exe}</string>
    <string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
</dict></plist>
"""

_DESKTOP = """[Desktop Entry]
Type=Application
Name=Conflux
Exec={exe} serve
X-GNOME-Autostart-enabled=true
"""


def _set_autostart(exe):
    if sys.platform == 'win32':
        if winreg is None:
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, _VALUE, 0, winreg.REG_SZ, f'"{exe}"')
        return
    if sys.platform == 'darwin':
        os.makedirs(os.path.dirname(_plist_path()), exist_ok=True)
        with open(_plist_path(), 'w', encoding='utf-8') as fh:
            fh.write(_PLIST.format(exe=exe))
        _run(['launchctl', 'unload', '-w', _plist_path()])
        _run(['launchctl', 'load', '-w', _plist_path()])
        return
    # linux: systemd user unit si hay systemctl; si no, XDG autostart
    if shutil.which('systemctl'):
        os.makedirs(os.path.dirname(_unit_path()), exist_ok=True)
        with open(_unit_path(), 'w', encoding='utf-8') as fh:
            fh.write(_UNIT.format(exe=exe))
        _run(['systemctl', '--user', 'daemon-reload'])
        _run(['systemctl', '--user', 'enable', 'conflux.service'])
        return
    os.makedirs(os.path.dirname(_desktop_path()), exist_ok=True)
    with open(_desktop_path(), 'w', encoding='utf-8') as fh:
        fh.write(_DESKTOP.format(exe=exe))


def _clear_autostart():
    if sys.platform == 'win32':
        if winreg is None:
            return
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, _VALUE)
        except OSError:
            pass
        return
    if sys.platform == 'darwin':
        _run(['launchctl', 'unload', '-w', _plist_path()])
        _unlink(_plist_path())
        return
    if shutil.which('systemctl') or os.path.exists(_unit_path()):
        _run(['systemctl', '--user', 'disable', '--now',
              'conflux.service'])
        _run(['systemctl', '--user', 'daemon-reload'])
        _unlink(_unit_path())
    _unlink(_desktop_path())


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def _clear_lnk():
    _unlink(_lnk_path())


def is_installed():
    """True si el autostart de la plataforma apunta al binario actual."""
    if sys.platform == 'win32':
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                v, _ = winreg.QueryValueEx(k, _VALUE)
            return _norm(v.strip('"')) == _norm(sys.executable)
        except OSError:
            return False
    if sys.platform == 'darwin':
        return os.path.exists(_plist_path())
    return os.path.exists(_unit_path()) or os.path.exists(_desktop_path())


def _write_pid():
    """Pidfile del proceso que sirve — uninstall lo usa para matar en posix."""
    try:
        os.makedirs(_dest_dir(), exist_ok=True)
        with open(_pidfile(), 'w', encoding='ascii') as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def _marker():
    return os.path.join(_dest_dir(), 'addon.ok')


def register_addon(force=False):
    """Registra el addon en Stremio tras /health; marca addon.ok al éxito.
    Sin force respeta la marca (un addon borrado a mano no se re-añade)."""
    from conflux.app.doctor import set_addon_state
    from conflux.infra import stremio          # import perezoso: red/leveldb
    if not force and os.path.exists(_marker()):
        return
    set_addon_state('registering')
    for _ in range(40):
        try:
            if urllib.request.urlopen(f'{PROXY}/health', timeout=2):   # nosec B310
                break
        except Exception:                      # noqa: BLE001
            time.sleep(.5)
    else:
        _log.info('install: proxy sin responder — addon no registrado')
        set_addon_state('remote_unreachable', last_error='proxy sin responder',
                        attempts=40)
        return
    st, msg = stremio.install(f'{PROXY}/manifest.json')
    _log.info('install: addon %s (%s)', st, msg)
    if st in ('installed', 'updated', 'already'):
        _atomic_json(_marker(), {'ok': True})
        set_addon_state('registered')
    elif st == 'manual':                     # sin authKey: guía deep-link
        set_addon_state('authentication_failed', last_error='sin authKey')
        deep = f'stremio://{PROXY.split("//", 1)[1]}/manifest.json'
        try:
            if sys.platform == 'win32':
                os.startfile(deep)
            elif sys.platform == 'darwin':
                _spawn(['open', deep])
            else:
                _spawn(['xdg-open', deep])
        except OSError:
            pass
    else:                                    # error: reintento en próximo arranque
        set_addon_state('retry_pending' if st == 'error' else 'failed',
                        last_error=str(msg)[:200])


def register_addon_bg(force=False):
    threading.Thread(target=register_addon, args=(force,),
                     daemon=True, name='addon-register').start()


def install_self():
    """Copia el exe a %LOCALAPPDATA%\\Conflux, fija autostart y relanza.
    Devuelve True si ESTE proceso debe seguir sirviendo."""
    dest = _dest_exe()
    try:
        os.makedirs(_dest_dir(), exist_ok=True)
    except OSError as e:                       # destino inescribible: servir en
        _log.warning('install: destino no escribible (%s) — sirviendo en sitio', e)
        return True                            # el lugar, sin autostart
    # la config del usuario viaja con la primera instalación; en upgrades
    # se respeta la existente junto al destino
    for name in ('config.json', 'sources.json'):
        src = os.path.join(os.path.dirname(sys.executable), name)
        tgt = os.path.join(_dest_dir(), name)
        if os.path.exists(src) and not os.path.exists(tgt):
            try:
                shutil.copy2(src, tgt)
            except OSError:
                pass
    _clear_lnk()                              # migra el autostart del wizard
    if _norm(sys.executable) != _norm(dest):
        if os.path.exists(dest):              # upgrade: la copia vieja puede
            kill_proxy()                      # estar corriendo y bloquearla
            time.sleep(.5)
        shutil.copy2(sys.executable, dest)
        if sys.platform != 'win32':
            os.chmod(dest, 0o755)      # nosec B103
    try:
        _set_autostart(dest)
        _log.info('install: autostart -> %s', dest)
    except OSError as e:                       # registro/plist bloqueado:
        _log.warning('install: autostart falló (%s)', e)   # sigue sirviendo
    if _norm(sys.executable) != _norm(dest):
        _spawn([dest])
        return False                          # sirve la copia instalada
    register_addon_bg(force=True)
    return True


def uninstall(say=_log.info):
    """Desmonta: proxy, autostart, addon remoto y el directorio de datos."""
    n = kill_proxy(say)
    _clear_autostart()
    _clear_lnk()
    say('install: %d proceso(s) detenidos, autostart quitado', n)
    try:
        from conflux.infra import stremio
        st, msg = stremio.remove(f'{PROXY}/manifest.json')
        say('install: addon stremio %s %s', st, msg)
    except Exception as e:                                   # noqa: BLE001
        say('install: addon remoto: %s', e)
    _unlink(_marker())
    _unlink(_pidfile())
    if sys.platform != 'win32':                # posix: el propio binario en
        shutil.rmtree(_dest_dir(), ignore_errors=True)   # ejecución se puede
        say('install: desinstalado')                       # desligar del fs
        return
    keep = {os.path.basename(_dest_exe())} \
        if _norm(sys.executable) == _norm(_dest_exe()) else set()
    try:                                       # idempotente: ya desinstalado = no-op
        entries = os.listdir(_dest_dir())
    except OSError:
        entries = []
    for f in entries:
        if f not in keep:
            _unlink(os.path.join(_dest_dir(), f))
    if keep:                                   # borrado diferido del propio exe
        _spawn(['cmd', '/c',
                f'ping -n 3 127.0.0.1 >nul & rmdir /s /q "{_dest_dir()}"'])
    else:                                      # uninstall desde otro exe:
        shutil.rmtree(_dest_dir(), ignore_errors=True)   # paridad posix
    say('install: desinstalado')


def _apply_log_level(argv):
    """--log-level=X | --log-level X (CLI > env > config > INFO)."""
    for i, a in enumerate(argv):
        lvl = a.split('=', 1)[1] if a.startswith('--log-level=') else (
            argv[i + 1] if a == '--log-level' and i + 1 < len(argv) else None)
        if lvl is not None:
            try:
                state.set_log_level(lvl)
            except ValueError as e:
                _log.warning('%s', e)
            return


def cli_entry(argv=None):
    """Decide el arranque del binario. Devuelve True si debe seguir a main()."""
    argv = list(sys.argv[1:] if argv is None else argv)
    _apply_log_level(argv)
    if 'rotate-admin-token' in argv:
        from conflux.core.cfg import rotate_admin_token
        rotate_admin_token()
        _log.info('admin_token rotado (ver config.json — no se muestra)')
        print('admin_token rotado (ver config.json — no se muestra)')
        return False
    for cmd in ('doctor', 'status'):
        if cmd in argv:
            from conflux.app.doctor import cli_diagnose
            raise SystemExit(cli_diagnose(argv))
    if not getattr(sys, 'frozen', False):
        return 'uninstall' not in argv         # dev: sirve salvo uninstall
    if 'uninstall' in argv:
        uninstall()
        return False
    if 'addon' in argv:
        register_addon_bg(force=True)
    elif 'install' in argv or not is_installed():
        if not install_self():
            return False                       # sirve la copia instalada
    else:
        register_addon_bg()                    # marca ausente -> reintenta
    _write_pid()                               # uninstall posix lo lee
    return True


if __name__ == '__main__':                   # depuración manual
    print(json.dumps({'installed': is_installed(),
                      'dest': _dest_exe()}, indent=2))
