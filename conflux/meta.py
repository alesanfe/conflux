"""GET /meta: meta canónico con enrichment y stale fallback."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import urllib.parse
import urllib.request
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import as_completed

from .feeds import _fix_metas
from .net import fetch
from .sources import res_of, res_types
from .state import _POOL, SOURCES, _cget, _cset, _cstale, _meta_c, manifests
from .stream import _ensure_collect


def _meta_race(futs):
    """Primer meta que responde gana (4.5s max); None si todos fallan."""
    try:
        for f in as_completed(futs, timeout=4.5):
            if (d := f.result()) is not None:
                return d
    except FutureTimeout:
        pass
    return None


def _meta_one(i, want, mid):
    """fetch meta de la fuente i si declara el recurso para 'want'."""
    m = manifests[i] if i < len(manifests) else None
    if m is not None and m.get('resources') and 'meta' not in res_of(m):
        return None
    ts = res_types(m, 'meta')
    if ts is not None and want not in ts:
        return None
    d = fetch(f"{SOURCES[i].base}/meta/{want}/{urllib.parse.quote(mid, safe=':')}.json", 4)
    return d if d and d.get('meta') else None


def handle_meta(stype, mid):
    """GET /meta — meta canónico + enrichment (wikidata, imdb, yt) con stale fallback."""
    key = (stype, mid)
    hit = _cget(_meta_c, key)
    if hit is not None:
        return hit
    # prefetch: en pelis el stream-id coincide con el meta-id — al abrir la
    # ficha ya se recolecta en background y el stream sale al instante
    if stype == 'movie' and ':' not in mid:
        _ensure_collect(key)
    # estrenos del calendario llegan como 'series' aunque sean pelis: fallback
    for want in ({stype, 'movie'} if stype == 'series' else {stype}):
        futs = [_POOL.submit(_meta_one, i, want, mid) for i in range(len(SOURCES))]
        d = _meta_race(futs)
        if d is not None:
            _fix_metas(d, deep=True)
            _cset(_meta_c, key, d)
            return d
    return _cstale(_meta_c, key) or {'meta': {}}
