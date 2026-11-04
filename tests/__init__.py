# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position,too-few-public-methods
"""Bootstrap de tests: src/ + repo root en sys.path y helpers compartidos."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'src'), '/tmp/pylibs'):
    if p not in sys.path:
        sys.path.insert(0, p)

import proxy as px

FIX = os.path.join(ROOT, 'tests', 'fixtures')


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
    También neutraliza el scrape UDP de torrent_health: los tests herméticos no
    deben tocar la red (hashes de fixture -> 0 seeds real los expulsaría)."""
    orig = []
    for name, mod in list(sys.modules.items()):
        if name.startswith('conflux.') and hasattr(mod, 'fetch'):
            orig.append((mod, 'fetch', mod.fetch))
            mod.fetch = fn
    th = sys.modules.get('conflux.infra.torrent_health')
    if th is not None:
        orig.append((th, '_scrape_hashes', th._scrape_hashes))
        th._scrape_hashes = lambda hashes: {}
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
