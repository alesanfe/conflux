"""Bootstrap de tests: src/ + repo root en sys.path y helpers compartidos."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'src'), '/tmp/pylibs'):
    if p not in sys.path:
        sys.path.insert(0, p)

import proxy as px

FIX = os.path.join(ROOT, 'fixtures')


def fixture(name):
    """fixtures/<name>.json -> lista de streams grabados (o [])."""
    p = os.path.join(FIX, f'{name}.json')
    if not os.path.exists(p):
        return []
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def patch_fetch(fn):
    """Sustituye `fetch` en TODOS los módulos conflux.* que lo tienen bindeado
    (cada módulo hace `from conflux.infra.net import fetch`). Devuelve restore()."""
    orig = []
    for name, mod in list(sys.modules.items()):
        if name.startswith('conflux.') and hasattr(mod, 'fetch'):
            orig.append((mod, mod.fetch))
            mod.fetch = fn
    def restore():
        for mod, f in orig:
            mod.fetch = f
    return restore

__all__ = ['FIX', 'ROOT', 'fixture', 'patch_fetch', 'px']
