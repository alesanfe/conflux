"""GET /meta: meta canónico con enrichment y stale fallback."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import urllib.parse
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import as_completed

from conflux.app.stream import _eff_disabled_for, _ensure_collect
from conflux.core.cfg import _cfgget
from conflux.core.state import _POOL, SOURCES, _cget, _cset, _cstale, _meta_c, manifests
from conflux.domain.metafix import _fix_metas, _posters_view, _rpdb
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
    """Tipos a probar EN ORDEN: el propio primero; series cae a movie (upstream
    declara pelis como series — tipo falsificado, R2). Set daba orden arbitrario:
    una peli con el mismo id podía ganar el fallback antes del tipo correcto."""
    return (stype, 'movie') if stype == 'series' else (stype,)


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
    """Canal M3U propio: meta local + programas de hoy (Native EPG).
    Lookup por _M3U_BYID — antes copiaba los ~2000 metas y los barría en
    lineal para encontrar uno. epg_meta copia el dict él mismo."""
    from conflux.domain.lists import _m3u_ensure, _M3U_BYID, _M3U, _iptv_dead, _m3u_adult
    from conflux.infra.epg import epg_meta
    _m3u_ensure()                                   # stat+parse si el fichero cambió
    ch = _M3U_BYID.get(mid)
    if ch is None or _iptv_dead(_M3U.get(mid, '')) or _m3u_adult(ch):
        return {'meta': {}}                         # mismo filtro que _m3u_out
    return {'meta': epg_meta(ch)}


def handle_meta(stype, mid):
    """GET /meta — meta canónico + enrichment (wikidata, imdb, yt) con stale fallback."""
    if mid.startswith('m3u:'):
        return _meta_tv(mid)
    key = (stype, mid)
    hit = _cget(_meta_c, key)
    if hit is not None:
        return _rpdb(_posters_view(hit))                 # rpdb es por perfil: fuera del caché
    # prefetch: en pelis el stream-id coincide con el meta-id — al abrir la
    # ficha ya se recolecta en background y el stream sale al instante.
    # stream_prefetch=False -> opt-out (privacidad/banda): no consulta fuentes
    # hasta que el usuario pulse reproducir de verdad.
    if stype == 'movie' and ':' not in mid and _cfgget('stream_prefetch', True):
        _ensure_collect(('movie', mid, tuple(sorted(_eff_disabled_for('movie')))))  # clave completa del collect
    # series->movie: upstreams que declaran una peli como series (tipo falsificado)
    for want in _meta_try(stype):
        futs = [_POOL.submit(_meta_one, i, want, mid) for i in range(len(SOURCES))]
        d = _meta_race(futs)
        if d is not None:
            _fix_metas(d, deep=True)
            _cset(_meta_c, key, d)                    # caché limpio: rpdb es por perfil
            return _rpdb(_posters_view(d))            # no-destructivo -> hay que usarlo
    return _cstale(_meta_c, key) or {'meta': {}}
