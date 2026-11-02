"""Manifests upstream, res_* e impedancia de ids (R1: parseo semántico)."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import json
from contextlib import suppress

from conflux.core.state import (
    _MF_CACHE,
    _POOL_BG,
    SOURCES,
    _cset,
    _open,
    as_dict,
    manifests,
)
from conflux.infra.net import fetch


def _load_manifests_cache():
    """Manifests persistidos del arranque anterior: el proxy responde al instante
    y refresh_manifests() en background trae la versión fresca (ventana fría ~0)."""
    with suppress(OSError, ValueError):
        disk = json.load(_open(_MF_CACHE))
        if len(disk) == len(SOURCES) and sum(1 for m in disk if m) >= len(SOURCES) // 2:
            return disk
    return None


def refresh_manifests():
    """Re-descarga manifests de todas las fuentes (reintenta cold-starts) y persiste."""
    fs = [_POOL_BG.submit(fetch, base + '/manifest.json', 10) for _, base in SOURCES]
    manifests[:] = [f.result() for f in fs]
    retry = [(i, _POOL_BG.submit(fetch, SOURCES[i].base + '/manifest.json', 20))
             for i, m in enumerate(manifests) if not m]
    for i, f in retry:
        m = f.result()
        if m:
            manifests[i] = m                    # cold-start (vercel) al segundo intento
    with suppress(OSError, TypeError, ValueError), open(_MF_CACHE, 'w', encoding='utf-8') as f:
        json.dump(manifests, f)


def res_of(m):
    """Lista normalizada de recursos del manifest (str o dict -> nombre)."""
    return [r if isinstance(r, str) else r.get('name') or r.get('id', '')
            for r in (m or {}).get('resources', [])]


def res_types(m, res):
    """Tipos declarados para un recurso; None = sin restricción declarada."""
    for r in (m or {}).get('resources', []):
        if isinstance(r, dict) and (r.get('name') or r.get('id')) == res:
            return set(r.get('types') or []) or None
    return None


def res_prefixes(m, res):
    """idPrefixes declarados para un recurso; None = sin restricción declarada."""
    for r in (m or {}).get('resources', []):
        if isinstance(r, dict) and (r.get('name') or r.get('id')) == res:
            return set(r.get('idPrefixes') or []) or None
    return None


_idmap     = {}            # (pref, base_id) -> imdb cacheado

_PREFIX_SRC = {'kitsu': 'Kitsu', 'mal': 'Kitsu', 'anilist': 'Kitsu', 'anidb': 'Kitsu',
               'tmdb': 'TMDB'}   # fuente que resuelve la meta (lleva imdb_id) por prefijo


def _resolve_meta(stype, pref, base_id):
    """meta de la fuente que resuelve el prefijo -> (imdb_id, videos), cacheado
    SIN TTL: el mapeo id->imdb es estable (años); _cset solo acota a 400."""
    key = (pref, base_id)
    ent = _idmap.get(key)
    if ent is None:
        base = next((b.base for b in SOURCES if b.name == _PREFIX_SRC[pref]), None)
        d = fetch(f'{base}/meta/{stype}/{pref}:{base_id}.json', 4) if base else None
        m = as_dict(d).get('meta') or {}
        ent = (m.get('imdb_id') or '', m.get('videos') or [])
        _cset(_idmap, key, ent)
        return ent
    return ent[1]


def _video_se(v):
    """vídeo del meta -> ':S:E' por campos o por su propio id."""
    s, e = str(v.get('season') or ''), str(v.get('episode') or '')
    if s.isdigit() and e.isdigit():
        return f':{int(s)}:{int(e)}'
    vsid = str(v.get('id') or '').split(':')
    return ':' + ':'.join(vsid[-2:]) if len(vsid) >= 3 else None


def _abs_ep(ep, vs):
    """'kitsu:49847:2' -> ':S:E' vía videos del meta (autoridad upstream)."""
    segs = ep.strip(':').split(':') if ep else []
    if len(segs) != 1 or not segs[0].isdigit() or not vs:
        return ep
    n = int(segs[0])
    if not 0 < n <= len(vs):
        return ep
    return _video_se(vs[n - 1]) or ep


def _resolve_sid(stype, sid):
    """kitsu:7442:1:1 -> tt2560140:1:1 — las fuentes entienden imdb, no kitsu/mal.
    Formato episodio-absoluto (kitsu:49847:2) -> mapeado a :S:E vía videos del meta."""
    pref = sid.split(':', 1)[0]
    if pref not in _PREFIX_SRC:
        return sid
    rest = sid.split(':', 1)[1]
    base_id = rest.split(':', 1)[0]
    ep = rest[len(base_id):]
    imdb, vs = _resolve_meta(stype, pref, base_id)
    if not imdb:
        return sid
    return imdb + _abs_ep(ep, vs)
