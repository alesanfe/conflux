# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position,too-few-public-methods
"""Bootstrap de tests: src/ + repo root en sys.path y helpers compartidos."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'src'), '/tmp/pylibs'):
    if p not in sys.path:
        sys.path.insert(0, p)

# cfg.load_config() corre AL IMPORTAR proxy -> maybe_provision() lanza un hilo
# daemon con red real (encrypt+manifest a elfhosted). En tests: stub no-op —
# sin fugas de red ni condiciones de carrera sobre SOURCES/manifests.
# test_mediafusion carga el módulo real por ruta para probar provision().
import types as _types
_mf_stub = _types.ModuleType('conflux.infra.mediafusion')
_mf_stub.maybe_provision = lambda: None
_mf_stub.provision = lambda: None
_mf_stub._once = False
sys.modules['conflux.infra.mediafusion'] = _mf_stub

# Log propio: el proxy real tiene conflux.log abierto -> en Windows la rotación
# del handler de tests peta con WinError 32. CONFLUX_LOG lo lee state.py al import.
os.environ.setdefault('CONFLUX_LOG',
                      os.path.join(ROOT, 'tests', '.test-conflux.log'))

import proxy as px

# _public_url hace DNS real (getaddrinfo): en tests lo stubamos con una versión
# que valida scheme + host sin resolver — las IPs literales peligrosas
# (169.254.x, multicast, reservadas) siguen bloqueadas para probar el guard SSRF.
import ipaddress as _ip
import urllib.parse as _up
import conflux.infra.net as _net


def _public_url_no_dns(url):
    try:
        p = _up.urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname:
            return False
        try:
            ip = _ip.ip_address(p.hostname)
            return not (ip.is_link_local or ip.is_multicast
                        or ip.is_reserved or ip.is_unspecified)
        except ValueError:
            return True                         # hostname: tests no resuelven
    except ValueError:
        return False


_net._real_public_url = _net._public_url      # test_net lo restaura por clase
_net._public_url = _public_url_no_dns

FIX = os.path.join(ROOT, 'tests', 'fixtures')

# torrent_health carga .seed-cache.json al importar — un run previo que haya
# dejado veredictos de hashes fake ('a'*40 = -2) contamina el baseline de
# _SEED para toda la suite (los tests de _hash_filter ven muertos ajenos).
# Baseline vacío + path a fixtures: _seed_save jamás escribe el repo.
import conflux.infra.torrent_health as _th
_th._SEED.clear()
_th._SEED_PATH = os.path.join(__import__('tempfile').gettempdir(),
                             '.test-seed-cache.json')

# fetch() duerme 0.3s real entre reintentos — cada test que ejercita un
# upstream fallido la paga. El backoff sigue existiendo en producción.
import conflux.infra.net as _net
_net._RETRY_SLEEP = 0


def fixture(name):
    """fixtures/<name>.json -> lista de streams grabados (o [])."""
    p = os.path.join(FIX, f'{name}.json')
    if not os.path.exists(p):
        return []
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def patch_fetch(fn):
    """Sustituye `fetch` en TODOS los módulos conflux.* que lo tienen bindeado
    (cada módulo hace `from conflux.infra.net import fetch`). Devuelve restore().
    También neutraliza torrent_health por completo: los tests herméticos no
    deben tocar la red ni heredar veredictos de disco (hashes de fixture ->
    veredicto -1/-2 real los expulsaría y _seed_save los persistiría)."""
    orig = []
    for name, mod in list(sys.modules.items()):
        if name.startswith('conflux.') and hasattr(mod, 'fetch'):
            orig.append((mod, 'fetch', mod.fetch))
            mod.fetch = fn
    th = sys.modules.get('conflux.infra.torrent_health')
    if th is not None:
        orig.append((th, '_scrape_submit', th._scrape_submit))
        th._scrape_submit = lambda items: None   # ni worker ni red ni _SEED
        orig.append((th, '_SEED', dict(th._SEED)))
        th._SEED.clear()                          # disco no contamina tests
    def restore():
        for mod, attr, f in orig:
            setattr(mod, attr, f)
    return restore


def fake_session(*script):
    """Sesión falsa para `patch.object(mod, '_session', return_value=s)`.
    script = [{'json':..., 'text':..., 'status':...}] consumido en orden
    por llamada (el último se repite). Devuelve (session, calls[])."""
    seq = list(script) or [{'json': None}]
    calls = []

    class _R:
        def __init__(self, spec):
            self._j = spec.get('json')
            self.text = spec.get('text', '')
            self.status_code = spec.get('status', 200)
            self.content = spec.get('content', b'')
            body = spec.get('content') or json.dumps(
                self._j if self._j is not None else self.text).encode()
            self.raw = type('Raw', (), {'read': lambda self_, *a, **k: body})()
        def json(self):
            return self._j
        def close(self):
            pass

    class _S:
        def _c(self, method, url, **kw):
            calls.append((method, url, kw))
            spec = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(spec, Exception):
                raise spec
            return _R(spec)
        def get(self, url, **kw):
            return self._c('GET', url, **kw)
        def post(self, url, **kw):
            return self._c('POST', url, **kw)
        def head(self, url, **kw):
            return self._c('HEAD', url, **kw)

    return _S(), calls


def patch_session(fn):
    """Sustituye `_session` en TODOS los módulos conflux.* que lo tienen
    bindeado (`from conflux.infra.net import _session`). Devuelve restore()."""
    orig = []
    for name, mod in list(sys.modules.items()):
        if name.startswith('conflux.') and hasattr(mod, '_session'):
            orig.append((mod, mod._session))
            mod._session = fn
    def restore():
        for mod, f_ in orig:
            mod._session = f_
    return restore


def cfg_swap(**kv):
    """Fija claves de CFG y devuelve (old, restore): restore() deja CFG
    exactamente como estaba. Uso: old, undo = cfg_swap(x='v') ... undo()."""
    import conflux.core.state as st
    old = dict(st.CFG)
    st.CFG.update(kv)
    def restore():
        st.CFG.clear()
        st.CFG.update(old)
    return old, restore


__all__ = ['FIX', 'ROOT', 'cfg_swap', 'fake_session', 'fixture', 'patch_fetch', 'px']


# ---------------------------------------------------------------------------
# CONFLUX-002 · Aislamiento global: cada test corre con snapshot/restore del
# estado mutable compartido + entorno + reloj. Se instala una envoltura en
# unittest.TestCase.run — equivale a un fixture autouse sin cambiar 50 archivos.
# Restaura contenido IN SITU (los módulos comparten referencias a los mismos
# contenedores; re-asignar no aislaría nada) y también re-bindea el nombre si
# el test rebindó el objeto entero.
# ---------------------------------------------------------------------------

_STATE_ATTRS = {
    'conflux.core.state': ['_fails', 'COOLDOWN', 'HEALTH', '_rtime', '_rts',
                           '_SERVED', '_cache', '_meta_c', '_cat_c',
                           '_DEAD_OPT', 'manifests', 'SOURCES', 'CFG',
                           '_DISABLED', '_SESS', '_LOGIN_TRIES',
                           'addon_state'],
    'conflux.app.stream': ['_URL_BAD', '_URL_OK', '_URL_PEND', '_REX_C'],
    'conflux.app.subs': ['_OS_TOK', '_SUBS_C'],
    'conflux.domain.lists': ['_M3U', '_M3U_METAS', '_M3U_BYID', '_M3U_TXT'],
    'conflux.infra.torznab': ['_TZ_IDS', '_TZ_C', '_TZ_DL'],
    'conflux.infra.net': ['_PUBLIC_URL_OK'],
    'conflux.infra.wikidata': ['_wd_c'],
    'conflux.infra.epg': ['_STATE'],
    'conflux.infra.torrent_health': ['_SEED', '_DHT_NODES'],
    'conflux.web.server': ['_LOGIN_TRIES'],
}


def _snapshot_state():
    import conflux.core.state as _st
    snap = {'env': dict(os.environ), 'clock': _st.CLOCK._d,
            'prof': None, 'objs': {}}
    cfg_mod = sys.modules.get('conflux.core.cfg')
    if cfg_mod is not None and hasattr(cfg_mod, '_PROF'):
        snap['prof'] = (cfg_mod._PROF,
                        getattr(cfg_mod._PROF, 'name', None),
                        dict(getattr(cfg_mod._PROF, 'cfg', None) or {}))
    for mod_name, attrs in _STATE_ATTRS.items():
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        for a in attrs:
            obj = getattr(mod, a, None)
            if isinstance(obj, dict):
                snap['objs'][(mod_name, a)] = ('d', obj, dict(obj))
            elif isinstance(obj, list):
                snap['objs'][(mod_name, a)] = ('l', obj, list(obj))
            elif isinstance(obj, set):
                snap['objs'][(mod_name, a)] = ('s', obj, set(obj))
    return snap


def _restore_state(snap):
    os.environ.clear()
    os.environ.update(snap['env'])
    _st = sys.modules.get('conflux.core.state')
    if _st is not None:
        _st.CLOCK._d = snap['clock']
        try:
            _st._REQ_ID.set('')
        except Exception:
            pass
    if snap['prof']:
        prof, name, cfg_ = snap['prof']
        prof.name, prof.cfg = name, cfg_
    for (mod_name, a), (kind, obj, val) in snap['objs'].items():
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        cur = getattr(mod, a, None)
        if cur is not obj:                  # el test rebindó el nombre
            setattr(mod, a, obj)
            cur = obj
        try:
            if kind == 'l':
                cur[:] = val
            else:
                cur.clear()
                cur.update(val)
        except (AttributeError, TypeError):
            setattr(mod, a, val if kind == 'l' else type(obj)(val))


import unittest as _ut


# ---------------------------------------------------------------------------
# Timing por test: cada test mide su duración real (perf_counter) y al final
# del proceso se informan los más lentos — los cuellos de botella del código
# bajo test saltan aquí. CONFLUX_TEST_TIMING=0 lo desactiva (CI ruidoso).
# ---------------------------------------------------------------------------
import atexit as _atexit
import time as _time

_TEST_TIMES: list = []


def _install_timing():
    if getattr(_ut.TestCase.run, '_conflux_timing', False):
        return
    _orig = _ut.TestCase.run

    def _timed_run(self, result=None):
        t0 = _time.perf_counter()
        try:
            return _orig(self, result)
        finally:
            _TEST_TIMES.append((_time.perf_counter() - t0, str(self)))

    _timed_run._conflux_timing = True
    _ut.TestCase.run = _timed_run


def _report_slowest(n=15):
    if not _TEST_TIMES:
        return
    top = sorted(_TEST_TIMES, reverse=True)[:n]
    total = sum(d for d, _ in _TEST_TIMES)
    print(f'\n-- timing de tests: {len(_TEST_TIMES)} tests, '
          f'{total:.1f}s total, top {n} más lentos --', file=sys.stderr)
    for dur, name in top:
        print(f'{dur:8.3f}s  {name}', file=sys.stderr)


if os.environ.get('CONFLUX_TEST_TIMING', '1') != '0':
    _install_timing()
    _atexit.register(_report_slowest)


def _install_isolation():
    if getattr(_ut.TestCase.run, '_conflux_iso', False):
        return
    _orig = _ut.TestCase.run

    def _iso_run(self, result=None):
        snap = _snapshot_state()
        try:
            return _orig(self, result)
        finally:
            _restore_state(snap)

    _iso_run._conflux_iso = True
    _ut.TestCase.run = _iso_run


_install_isolation()
