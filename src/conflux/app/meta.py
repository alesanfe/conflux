"""GET /meta: meta canónico con enrichment y stale fallback."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import urllib.parse
import urllib.request
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import as_completed

from conflux.app.stream import _ensure_collect
from conflux.core.state import _POOL, SOURCES, _cget, _cset, _cstale, _meta_c, manifests
from conflux.domain.metafix import _fix_metas
from conflux.domain.sources import res_of, res_types
from conflux.infra.net import fetch


def _meta_race(futs):
    """Primer meta que responde gana (4.5s max); None si todos fallan."""
    try:
        for f in as_completed(futs, timeout=4.5):
            if (d := f.result()) is not None:
                return d
    except FutureTimeout:
        pass
    return None


def _meta_try(stype):
    """Tipos a probar: series cae a movie (estrenos del calendario llegan como series)."""
    return {stype, 'movie'} if stype == 'series' else {stype}


def _meta_one(i, want, mid):
    """fetch meta de la fuente i si declara el recurso para 'want'."""
    m = manifests[i] if i < len(manifests) else None
    if m is not None and m.get('resources') and 'meta' not in res_of(m):
        return None
    ts = res_types(m, 'meta')
    if ts is not None and want not in ts:
        return None
    d = fetch(f"{SOURCES[i].base}/meta/{want}/{urllib.parse.quote(mid, safe=':')}.json", 4)
    return d if isinstance(d, dict) and d.get('meta') else None


def _meta_tv(mid):
    """Canal M3U propio: meta local + programas de hoy (Native EPG)."""
    from conflux.domain.lists import _m3u_metas   # import perezoso: evita ciclo
    from conflux.infra.epg import epg_meta
    ch = next((x for x in _m3u_metas() if x['id'] == mid), None)
    return {'meta': epg_meta(ch) if ch else {}}


def handle_meta(stype, mid):
    """GET /meta — meta canónico + enrichment (wikidata, imdb, yt) con stale fallback."""
    if mid.startswith('m3u:'):
        return _meta_tv(mid)
    key = (stype, mid)
    hit = _cget(_meta_c, key)
    if hit is not None:
        return hit
    # prefetch: en pelis el stream-id coincide con el meta-id — al abrir la
    # ficha ya se recolecta en background y el stream sale al instante
    if stype == 'movie' and ':' not in mid:
        _ensure_collect(key)
    # estrenos del calendario llegan como 'series' aunque sean pelis: fallback
    for want in _meta_try(stype):
        futs = [_POOL.submit(_meta_one, i, want, mid) for i in range(len(SOURCES))]
        d = _meta_race(futs)
        if d is not None:
            _fix_metas(d, deep=True)
            _cset(_meta_c, key, d)
            return d
    return _cstale(_meta_c, key) or {'meta': {}}
