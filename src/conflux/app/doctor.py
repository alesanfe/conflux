"""Diagnóstico CLI (CONFLUX-021..024): `conflux status` y `conflux doctor`.

- status: resumen operativo (fuentes, breakers, addon, rutas).
- doctor: batería de checks con severidad y códigos de salida estables.
- --json: salida estable para automatización.

Códigos de salida: 0 ok · 1 warnings · 2 config inválida · 3 servicio caído ·
4 fallo de autenticación · 5 error interno.
"""
import json
import os
import shutil
import sys
import time
import urllib.request

from conflux.core.state import _LOG_FILE, _ROOT, SOURCES, manifests

PROXY = 'http://127.0.0.1:9700'

ADDON_STATES = ('not_attempted', 'registering', 'registered', 'retry_pending',
                'authentication_failed', 'remote_unreachable', 'failed')


def _version():
    try:
        import importlib.metadata
        return importlib.metadata.version('conflux')
    except Exception:
        return '0+local'


# ---------- estado estructurado del addon (CONFLUX-022) ----------

def _addon_state_file():
    from conflux.app.install import _dest_dir
    return os.path.join(_dest_dir(), '.addon-state.json')


def addon_state():
    """Lee el estado del registro del addon (o lo deriva de addon.ok)."""
    try:
        with open(_addon_state_file(), encoding='utf-8') as fh:
            st = json.load(fh)
            if st.get('state') in ADDON_STATES:
                return st
    except (OSError, ValueError):
        pass
    try:
        from conflux.app.install import _marker
        if os.path.exists(_marker()):
            return {'state': 'registered', 'via': 'legacy marker'}
    except Exception:
        pass
    return {'state': 'not_attempted'}


def set_addon_state(state_name, **kw):
    """Persiste el estado del registro — nunca credenciales ni URLs privadas."""
    if state_name not in ADDON_STATES:
        return
    from conflux.core.state import _atomic_json
    rec = {'state': state_name, 'ts': int(kw.pop('ts', 0) or time.time()),
           'version': _version(), 'target': '127.0.0.1:9700'}
    rec.update({k: v for k, v in kw.items() if v is not None})
    try:
        _atomic_json(_addon_state_file(), rec)
    except OSError:
        pass


# ---------- sondeos ----------

def _http(path, timeout=3):
    try:
        with urllib.request.urlopen(f'{PROXY}{path}', timeout=timeout) as r:  # nosec B310
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def _port_free(port=9700):
    import socket
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def collect_status():
    """Resumen operativo — datos, no formateo."""
    code, health = _http('/health')
    running = code == 200
    # Breakers/degradados viven en el proceso del SERVICIO: leer /health,
    # no los dicts locales (vacíos en este proceso CLI).
    hb = health if isinstance(health, dict) else {}
    open_brk = sorted(h for h, strikes in
                      (hb.get('fetch_fails') or {}).items()
                      if isinstance(strikes, int) and strikes >= 4)
    degraded = sorted(n for n, h in (hb.get('sources') or {}).items()
                      if isinstance(h, dict) and h.get('streak', 0) >= 3)
    try:
        cache_age = int(time.time() - os.path.getmtime(
            os.path.join(_ROOT, '.manifests-cache.json')))
    except OSError:
        cache_age = None
    return {'version': _version(),
            'running': running,
            'url': PROXY,
            'sources': {'total': len(SOURCES),
                        'with_manifest': sum(1 for m in manifests if m),
                        'degraded': degraded},
            'breakers_open': sorted(open_brk),
            'addon': addon_state(),
            'cache_age_s': cache_age,
            'log': _LOG_FILE,
            'config': os.path.join(_ROOT, 'config.json')}


def run_status(json_out=False, out=print):
    """`conflux status [--json]` -> 0. Resumen operativo legible o JSON."""
    st = collect_status()
    if json_out:
        out(json.dumps(st, indent=2, sort_keys=True))
        return 0
    s = st['sources']
    out(f"conflux {st['version']} — {'corriendo' if st['running'] else 'parado'}")
    out(f"  url: {st['url']}  fuentes: {s['with_manifest']}/{s['total']}"
        + (f"  degradadas: {', '.join(s['degraded'])}" if s['degraded'] else ''))
    out(f"  breakers abiertos: {len(st['breakers_open'])}"
        + (f" ({', '.join(st['breakers_open'])})" if st['breakers_open'] else ''))
    out(f"  addon: {st['addon'].get('state')}")
    if st['cache_age_s'] is not None:
        out(f"  caché manifests: hace {st['cache_age_s']}s")
    out(f"  log: {st['log']}")
    out(f"  config: {st['config']}")
    return 0


def _cfg_check():
    """config.json: existe, parsea, perms saneados -> (nivel, msg)."""
    path = os.path.join(_ROOT, 'config.json')
    if not os.path.exists(path):
        return 'warn', 'config.json no existe — defaults'
    try:
        with open(path, encoding='utf-8') as fh:
            json.load(fh)
    except (OSError, ValueError) as e:
        return 'fail', f'config.json ilegible: {e}'
    if sys.platform != 'win32':
        mode = os.stat(path).st_mode & 0o777
        if mode & 0o077:
            return 'warn', f'config.json perms {mode:o} — recomendado 600'
    return 'ok', 'config.json válida'


def collect_doctor():
    """Lista de checks: (id, nivel ok|warn|fail, mensaje)."""
    checks = []
    checks.append(('version', 'ok', f'conflux {_version()} '
                   f'({sys.platform}, py{sys.version_info.major}.'
                   f'{sys.version_info.minor})'))
    checks.append(('config', *_cfg_check()))
    code, _body = _http('/health')
    running = code == 200
    if running:
        checks.append(('service', 'ok', f'{PROXY} responde'))
    else:
        checks.append(('service', 'fail',
                       f'{PROXY} no responde — ¿conflux corriendo?'))
    checks.append(('port', 'ok' if not _port_free() or running else 'warn',
                   'puerto 9700 ocupado' if not _port_free()
                   else 'puerto 9700 libre'))
    ad = addon_state()
    lvl = {'registered': 'ok', 'not_attempted': 'warn',
           'registering': 'ok', 'retry_pending': 'warn'}.get(
               ad.get('state'), 'fail')
    checks.append(('addon', lvl, f"estado: {ad.get('state')}"
                   + (f" ({ad.get('last_error')})" if ad.get('last_error') else '')))
    try:
        from conflux.infra.stremio import find_auth
        _k, prov = find_auth()
        checks.append(('authkey', 'ok' if prov else 'warn',
                       f'credencial vía {prov}' if prov
                       else 'sin authKey — registro manual (deep-link)'))
    except Exception:
        checks.append(('authkey', 'warn', 'no se pudo evaluar la credencial'))
    st = collect_status()
    s = st['sources']
    checks.append(('sources', 'ok' if s['with_manifest'] else 'warn',
                   f"{s['with_manifest']}/{s['total']} con manifest"))
    checks.append(('breakers', 'ok' if not st['breakers_open'] else 'warn',
                   f"{len(st['breakers_open'])} abiertos"
                   + (f": {', '.join(st['breakers_open'])}"
                      if st['breakers_open'] else '')))
    age = st['cache_age_s']
    checks.append(('cache', 'ok' if age is not None else 'warn',
                   f'caché de hace {age}s' if age is not None else 'sin caché'))
    try:
        du = shutil.disk_usage(_ROOT)
        free_mb = du.free // (1 << 20)
        checks.append(('disk', 'ok' if free_mb > 100 else 'warn',
                       f'{free_mb} MiB libres'))
    except OSError:
        checks.append(('disk', 'warn', 'no se pudo medir el disco'))
    try:
        open(_LOG_FILE, 'a', encoding='utf-8').close()
        checks.append(('log', 'ok', f'escribible: {_LOG_FILE}'))
    except OSError:
        checks.append(('log', 'warn', f'no escribible: {_LOG_FILE}'))
    try:
        from conflux.app.install import is_installed
        checks.append(('autostart', 'ok' if is_installed() else 'warn',
                       'autostart instalado' if is_installed()
                       else 'sin autostart (dev/sin instalar)'))
    except Exception:
        checks.append(('autostart', 'warn', 'no evaluable'))
    return checks


_LEVEL_EXIT = {'ok': 0, 'warn': 1, 'fail': 3}


def run_doctor(json_out=False, out=print):
    checks = collect_doctor()
    if json_out:
        out(json.dumps({'version': _version(),
                        'checks': [{'id': i, 'level': lvl, 'msg': m}
                                   for i, lvl, m in checks]},
                       indent=2))
    else:
        for i, lvl, m in checks:
            mark = {'ok': 'OK ', 'warn': 'AVISO', 'fail': 'FALLO'}[lvl]
            out(f'  [{mark:5}] {i:10} {m}')
    worst = 0
    for i, lvl, _m in checks:
        worst = max(worst, _LEVEL_EXIT[lvl])
        if i == 'config' and lvl == 'fail':
            worst = 2
        if i == 'authkey' and lvl == 'fail':
            worst = 4
    return worst


def cli_diagnose(argv, out=print):
    """'doctor'/'status' + '--json' -> código de salida."""
    json_out = '--json' in argv
    if 'doctor' in argv:
        return run_doctor(json_out, out)
    return run_status(json_out, out)
