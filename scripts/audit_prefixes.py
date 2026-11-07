#!/usr/bin/env python3
"""Auditoría de prefijos (STM-002 / R3): lo que los upstreams DECLARAN servir
vs lo que nuestro manifest EMITE.

R3: Stremio filtra en cliente — si una fuente declara ids 'mx:'/'kitsu:'/…
para stream/meta/catalog y nuestro manifest no declara ese prefijo, ese
contenido es INVISIBLE (la app ni siquiera pide al proxy). Este script lo
detecta sin contenido: compara la unión de idPrefixes de cada fuente
(normalizados, sin ':' final) contra build_manifest()['idPrefixes'] y
_PREFIX_SRC (los prefijos que sabemos resolver a imdb).

Salida:
  FUENTE            DECLARADOS(upstream)          FALTA EN MANIFEST   FALTA RESOLVER
  Torrentio         tt                             -                   -
  ...
  OK / WARN / FAIL: FAIL si algún prefijo upstream queda fuera de nuestro
  idPrefixes (contenido invisible); WARN si el prefijo entra pero no tiene
  fuente resolutora en _PREFIX_SRC (meta/stream alien no traducible a tt).

Uso: python3 scripts/audit_prefixes.py
  --live    ignora .manifests-cache.json y pide /manifest.json a cada fuente
  --strict  exit 1 ante cualquier WARN (para CI)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from conflux.core.state import _MF_CACHE, SOURCES, manifests   # noqa: E402
from conflux.domain.manifest import build_manifest            # noqa: E402
from conflux.domain.sources import _PREFIX_SRC, _load_manifests_cache, res_prefixes  # noqa: E402
from conflux.infra.net import fetch                           # noqa: E402


def _declared(m):
    """Manifest upstream -> {prefijo_norm: set(recursos)}."""
    out: dict = {}
    for r in (m or {}).get('resources', []):
        if not isinstance(r, dict):
            continue
        res = r.get('name') or r.get('id') or '?'
        for p in res_prefixes(m, res) or ():
            out.setdefault(p, set()).add(res)
    return out


def main():
    live = '--live' in sys.argv
    strict = '--strict' in sys.argv
    if live:
        ours = {SOURCES[i].name: fetch(b + '/manifest.json', 10)
                for i, (_, b) in enumerate(SOURCES)}
        mf = [ours[s.name] for s in SOURCES]
    else:
        cached = _load_manifests_cache()
        mf = cached or manifests
        ours = {SOURCES[i].name: (mf[i] if i < len(mf) else None)
                for i in range(len(SOURCES))}
        if cached is None:
            print('sin .manifests-cache.json — usa --live o arranca el proxy')
            return 2

    own = set(build_manifest().get('idPrefixes') or [])
    resolvers = set(_PREFIX_SRC)
    rows, missing, unresolved = [], [], []
    for i, (name, _base) in enumerate(SOURCES):
        m = ours.get(name) if not live else mf[i]
        dec = _declared(m)
        if not dec:
            rows.append((name, '-', '-', '-'))
            continue
        prefs = sorted(dec)
        not_ours = [p for p in prefs if p not in own]
        not_res = [p for p in prefs if p not in resolvers and p != 'tt']
        if not_ours:
            missing.append((name, not_ours))
        if not_res:
            unresolved.append((name, not_res))
        rows.append((name, ','.join(prefs) or '-',
                     ','.join(not_ours) or '-', ','.join(not_res) or '-'))
    w = max(len(r[0]) for r in rows) if rows else 8
    for name, prefs, no, nr in rows:
        print(f'{name:<{w}}  {prefs:<40}  {no:<22}  {nr}')
    print()
    if missing:
        print('FAIL — prefijos declarados upstream fuera de nuestro manifest:')
        for n, ps in missing:
            print(f'  {n}: {", ".join(ps)}  (contenido invisible — R3)')
        return 1
    if unresolved:
        # informativo: prefijos que no traducimos a tt — su fuente los sirve
        # directo (mx=Stravo/MediaFusion, dsx=DesiFlix); no es defecto por sí solo
        print('INFO — prefijos sin traducción a tt (servidos por su fuente):')
        for n, ps in unresolved:
            print(f'  {n}: {", ".join(ps)}')
        if strict:
            return 1
    print('OK — idPrefixes cubren todo lo declarado upstream')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
