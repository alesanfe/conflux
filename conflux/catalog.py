"""Routing /catalog: sintéticos, listas, búsqueda, anime-rewrite."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import urllib.parse
import urllib.request

from .calendar import _estrenos_cal
from .cfg import _eff_disabled
from .lists import _list_metas, _m3u_metas
from .manifest import _GRP, _LEGACY, _SYNTH, _UTYPE, _YR_OPTS
from .metafix import _fix_metas
from .net import fetch
from .sources import _resolve_sid
from .state import _POOL, _POOL_BG, SOURCES, _cat_c, _cget, _cset, _cstale, manifests
from .trakt import _recom


def _cat_searchable(src, c, t):
    """El catálogo es del tipo pedido (real o upstream) y soporta 'search'."""
    ct = _UTYPE.get(f"s{src.name.lower()}_{c['type']}_{c['id']}", c['type'])
    if t not in (ct, c['type']):
        return False
    extras = [e.get('name') for e in (c.get('extra') or [])]
    return 'search' in extras or 'search' in (c.get('extraSupported') or [])


def _search_targets(t):
    """Catálogos upstream del tipo que soportan 'search' -> (base, utype, catid)."""
    targets = []
    for i, m in enumerate(manifests):
        if not m or SOURCES[i].name in _eff_disabled():
            continue
        for c in m.get('catalogs') or []:
            if _cat_searchable(SOURCES[i], c, t):
                targets.append((SOURCES[i].base, c['type'], c['id']))
    return targets


def _busqueda(utype, extra):
    """Búsqueda global: fan-out a TODOS los catálogos del tipo que soportan
    'search' upstream y dedup por id canónico (imdb si resoluble)."""
    q = dict(urllib.parse.parse_qsl(extra or '')).get('search')
    if not q:
        return {'metas': []}
    targets = _search_targets('anime' if utype == 'anime' else utype)
    seen, out = set(), []
    e = urllib.parse.urlencode({'search': q})
    for d in _POOL_BG.map(lambda x: fetch(f"{x[0]}/catalog/{x[1]}/{x[2]}/{e}.json", 8), targets):
        _search_merge(d, seen, out)
    return {'metas': out}


def _search_merge(d, seen, out):
    """Metas de una respuesta -> out, dedup por id."""
    for m in (d or {}).get('metas') or []:
        if not m or m.get('id') in seen:
            continue
        seen.add(m['id'])
        out.append(m)


def _cat_route(cid, stype):
    """cid -> (base, utype, real). Raise ValueError/StopIteration si no mapea."""
    slug, rest = cid.split('_', 1)
    s = slug[1:]
    name = _LEGACY[int(s)] if s.isdigit() else s              # s6_ viejo → NoTorrent por nombre
    try:
        base = next(b for n, b in SOURCES if n.lower() == name.lower())
    except StopIteration:
        if cid not in _SYNTH:
            raise                       # slug desconocido y no sintético
        base = None                                       # sintético sin fuente (Buscar, M3U)
    utype, _, real = rest.partition('_')
    if utype not in {'movie', 'series', 'anime', 'channel', 'tv'}:
        real, utype = rest, _UTYPE.get(cid, stype)  # id antiguo sin tipo embebido
    return base, utype, real


def _cat_grp_extra(cid, extra):
    """Agrupado: genre -> catálogo upstream elegido + picker año -> genre=año."""
    kv = dict(urllib.parse.parse_qsl(extra or ''))
    g = kv.pop('genre', None)
    y = kv.pop('year', None)                          # picker año (members con genre=año)
    utype, real, wants = _GRP[cid].get(g) or next(iter(_GRP[cid].values()))
    if wants:
        kv['genre'] = y or _YR_OPTS[cid][0]     # sin año: el más reciente
    return utype, real, urllib.parse.urlencode(kv, safe=',')


def _synth_recom(utype, extra, _defx, _key):
    """recomendaciones trakt (cache en _cat_c: related es caro)."""
    hit2 = _cget(_cat_c, ('recom', utype))
    if hit2 is None:
        hit2 = _recom(utype)
        _cset(_cat_c, ('recom', utype), hit2)
    return hit2, utype, extra

def _synth_cal(utype, extra, _defx, key):
    """estrenos del calendario (extra=ids pedidos; respuesta cacheada)."""
    hit2 = _estrenos_cal(extra)
    if hit2 is not None:
        _cset(_cat_c, key, hit2)
    return hit2 or {'metas': []}, utype, extra

_SYNTH_H = {'_busqueda': lambda utype, extra, defx, key: (_busqueda(utype, extra), utype, extra),
            '_recom': _synth_recom,
            '_list': lambda utype, extra, defx, key: (_list_metas(defx, utype), utype, extra),
            '_m3u': lambda utype, extra, defx, key: ({'metas': _m3u_metas()}, utype, extra),
            '_estrenos_cal': _synth_cal}

def _cat_synth(cid, utype, extra, key):
    """Sintéticos con handler propio -> respuesta; None si sigue el flujo normal."""
    utype, real, defx = _SYNTH[cid]
    h = _SYNTH_H.get(real)
    if h:
        resp, utype2, extra2 = h(utype, extra, defx, key)
        return resp, utype2, real, extra2
    return None, utype, real, extra if extra else defx


def _cat_xlate_ids(utype, extra):
    """ids no-tt -> tt p/ Cinemeta (calendario/biblioteca)."""
    kv = dict(urllib.parse.parse_qsl(extra))
    for k in ('lastVideosIds', 'calendarVideosIds'):
        if k in kv:
            ids = kv[k].split(',')[:100]          # optionsLimit:100 — fan-out acotado
            kv[k] = ','.join(_POOL.map(lambda i: _resolve_sid(utype, i.strip()), ids))
    return urllib.parse.urlencode(kv, safe=',')   # comas literales: sin ellas upstream ve 1 id mutilado


def _cat_resolve(cid, stype, extra, key):
    """cid -> (base, utype, real, extra) aplicando agrupados/sintéticos/rewrite.
    Devuelve dict-respuesta si un sintético respondió directo."""
    base, utype, real = _cat_route(cid, stype)
    if cid in _GRP:                                       # agrupado: genre -> catálogo upstream
        utype, real, extra = _cat_grp_extra(cid, extra)
    elif cid in _SYNTH:                                   # sintético: ruta upstream predefinida
        resp, utype, real, extra = _cat_synth(cid, utype, extra, key)
        if resp is not None:
            return resp
    if extra:                                            # ids no-tt -> tt p/ Cinemeta (calendario/biblioteca)
        extra = _cat_xlate_ids(utype, extra)
    return base, utype, real, extra


def handle_catalog(stype, cid, extra):
    """GET /catalog — routing: sintéticos, listas externas, rewrite anime y upstream."""
    key = (stype, cid, extra)
    hit = _cget(_cat_c, key)
    if hit is not None:
        return hit
    try:
        resolved = _cat_resolve(cid, stype, extra, key)
    except (ValueError, StopIteration, IndexError):
        return {'metas': []}
    if isinstance(resolved, dict):                            # sintético respondió directo
        return resolved
    base, utype, real, extra = resolved
    tail = f"/{urllib.parse.quote(extra, safe='=&,:')}" if extra else ''
    d = fetch(f"{base}/catalog/{utype}/{urllib.parse.quote(real, safe='')}{tail}.json", 7)
    if d:
        _fix_metas(d)
        _cset(_cat_c, key, d)
        return d
    return _cstale(_cat_c, key) or {'metas': []}   # upstream caído: sirve stale
