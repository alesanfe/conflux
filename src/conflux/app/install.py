"""Self-install del exe frozen (Windows): `conflux.exe` solo monta el entorno.

Flujo:
- primer arranque (o `conflux.exe install`): se copia a
  %LOCALAPPDATA%\\Conflux\\conflux.exe, registra autostart (Run key HKCU),
  elimina el Conflux.lnk legado y relanza la copia instalada; el registro del
  addon en Stremio corre en un hilo tras /health (marca `addon.ok` para no
  reinstalar un addon que el usuario quitó a mano).
- `conflux.exe uninstall`: mata :9700, quita Run key + lnk, desregistra el
  addon remoto y borra %LOCALAPPDATA%\\Conflux (incluido el propio exe, con
  borrado diferido si se está ejecutando desde ahí).
- `conflux.exe addon`: re-registra el addon (si el usuario lo quitó y quiere
  recuperarlo).
- `conflux.exe serve` / sin args ya instalado: solo sirve.

Fuera de Windows o sin frozen (dev `python proxy.py`) es no-op.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

try:
    import winreg
except ImportError:                    # linux/mac — el módulo es no-op
    winreg = None                      # type: ignore[assignment]

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


def _dest_dir():
    la = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return os.path.join(la, 'Conflux')


def _dest_exe():
    return os.path.join(_dest_dir(), 'conflux.exe')


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


def kill_proxy(say=_log.info):
    """Mata lo que escuche en :9700 y otros conflux.exe (excepto este)."""
    killed = set()
    try:
        res = _run('netstat -ano')
        out = res.stdout.decode('mbcs', 'replace') \
            if isinstance(res.stdout, bytes) else res.stdout
        killed.update(_netstat_pids(out))
    except Exception as e:                                     # noqa: BLE001
        say('install: netstat falló (%s)', e)
    own = os.getpid()
    for pid in killed - {own}:
        _run(['taskkill', '/PID', str(pid), '/F'])
    _run(['taskkill', '/IM', 'conflux.exe', '/F',
          '/FI', f'PID ne {own}'])
    return len(killed - {own})


def _set_autostart(exe):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, _VALUE, 0, winreg.REG_SZ, f'"{exe}"')


def _clear_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _VALUE)
    except OSError:
        pass


def _clear_lnk():
    try:
        os.unlink(_lnk_path())
    except OSError:
        pass


def is_installed():
    """True si el Run key apunta al ejecutable actual."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, _VALUE)
        return _norm(v.strip('"')) == _norm(sys.executable)
    except OSError:
        return False


def _marker():
    return os.path.join(_dest_dir(), 'addon.ok')


def register_addon(force=False):
    """Registra el addon en Stremio tras /health; marca addon.ok al éxito.
    Sin force respeta la marca (un addon borrado a mano no se re-añade)."""
    from conflux.infra import stremio          # import perezoso: red/leveldb
    if not force and os.path.exists(_marker()):
        return
    for _ in range(40):
        try:
            if urllib.request.urlopen(f'{PROXY}/health', timeout=2):
                break
        except Exception:                      # noqa: BLE001
            time.sleep(.5)
    else:
        _log.info('install: proxy sin responder — addon no registrado')
        return
    st, msg = stremio.install(f'{PROXY}/manifest.json')
    _log.info('install: addon %s (%s)', st, msg)
    if st in ('installed', 'updated', 'already'):
        _atomic_json(_marker(), {'ok': True})
    elif st == 'manual' and sys.platform == 'win32':
        os.startfile(f'stremio://{PROXY.split("//", 1)[1]}/manifest.json')


def register_addon_bg(force=False):
    threading.Thread(target=register_addon, args=(force,),
                     daemon=True, name='addon-register').start()


def install_self():
    """Copia el exe a %LOCALAPPDATA%\\Conflux, fija autostart y relanza.
    Devuelve True si ESTE proceso debe seguir sirviendo."""
    dest = _dest_exe()
    os.makedirs(_dest_dir(), exist_ok=True)
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
    _set_autostart(dest)
    _log.info('install: autostart -> %s', dest)
    if _norm(sys.executable) != _norm(dest):
        subprocess.Popen([dest], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_DETACHED, close_fds=True)
        return False                          # sirve la copia instalada
    register_addon_bg(force=True)
    return True


def uninstall(say=_log.info):
    """Desmonta: proxy, autostart, addon remoto y %LOCALAPPDATA%\\Conflux."""
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
    try:
        os.unlink(_marker())
    except OSError:
        pass
    keep = {'conflux.exe'} if _norm(sys.executable) == _norm(_dest_exe()) \
        else set()
    for f in os.listdir(_dest_dir()):
        if f not in keep:
            try:
                os.unlink(os.path.join(_dest_dir(), f))
            except OSError:
                pass
    if keep:                                   # borrado diferido del propio exe
        subprocess.Popen(
            f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{_dest_dir()}"',
            shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=_DETACHED, close_fds=True)
    say('install: desinstalado')


def cli_entry(argv=None):
    """Decide el arranque del exe. Devuelve True si debe seguir a main()."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if winreg is None or not getattr(sys, 'frozen', False):
        return 'uninstall' not in argv         # dev/linux: sirve salvo uninstall
    if 'uninstall' in argv:
        uninstall()
        return False
    if 'addon' in argv:
        register_addon_bg(force=True)
        return True
    if 'install' in argv or not is_installed():
        return install_self()
    register_addon_bg()                        # marca ausente -> reintenta 1 vez
    return True


if __name__ == '__main__':                   # depuración manual
    print(json.dumps({'installed': is_installed(),
                      'dest': _dest_exe()}, indent=2))
