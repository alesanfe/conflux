"""Routing /catalog: sintéticos, listas, búsqueda, anime-rewrite."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import threading
import time
import urllib.parse
from concurrent.futures import as_completed

from conflux.core.cfg import _cfgget, _eff_disabled
from conflux.core.state import (
    _DEAD_OPT,
    _POOL,
    SOURCES,
    _cat_c,
    _cget,
    _cset,
    _cstale,
    as_dict,
    manifests,
)
from conflux.domain.calendar import _estrenos_cal
from conflux.domain.lists import _list_metas, _m3u_metas
from conflux.infra.epg import epg_metas
from conflux.domain.manifest import _GRP, _LEGACY, _SYNTH, _UTYPE, _YR_OPTS
from conflux.domain.metafix import _fix_metas, _rpdb
from conflux.domain.sources import _PREFIX_SRC, _resolve_sid
from conflux.domain.text import _id_prefix
from conflux.infra.net import fetch
from conflux.infra.trakt import _recom


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
    'search' upstream y dedup por id canónico (imdb si resoluble).
    Deadline global ~10s: los stragglers no retienen la respuesta (sus
    resultados quedan en caché para la próxima vez igualmente)."""
    q = dict(urllib.parse.parse_qsl(extra or '')).get('search')
    if not q:
        return {'metas': []}
    targets = _search_targets('anime' if utype == 'anime' else utype)
    seen: set = set()
    out: list = []
    e = urllib.parse.urlencode({'search': q})
    futs = [_POOL.submit(fetch, f"{b}/catalog/{t}/{c}/{e}.json", 8)
            for b, t, c in targets]
    t0 = time.time()
    done = 0
    try:
        for f in as_completed(futs, timeout=10):
            done += 1
            try:
                _search_merge(f.result(), utype, seen, out)
            except Exception:
                continue
            el = time.time() - t0
            if el > 2 and len(futs) - done <= 3:    # early-done: stragglers ~muertos
                break                             # como _collect: no retener por 3
            if el > 4 and len(out) >= 40:          # resultado sólido sin esperar más
                break
    except TimeoutError:
        pass                                    # deadline: responde con lo que hay
    return {'metas': out}


def _search_key(utype, m):
    """Clave dedup del meta: id canónico (tt resolviendo prefijos alien).

    imdb_id del propio meta es gratis; tt y prefijos no mapeables también;
    solo los alien sin imdb_id pasan por _resolve_sid (fetch cacheado)."""
    mid = m['id']
    if m.get('imdb_id'):
        return m['imdb_id']
    return _resolve_sid(utype, mid) if _id_prefix(mid) in _PREFIX_SRC else mid


def _search_merge(d, utype, seen, out):
    """Metas de una respuesta -> out, dedup por id canónico (tt si resoluble)."""
    metas = [m for m in as_dict(d).get('metas') or []
             if isinstance(m, dict) and m.get('id')]
    keys = _POOL.map(lambda m: _search_key(utype, m), metas)
    for m, k in zip(metas, keys):
        if k in seen:
            continue
        seen.add(k)
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
    if g and (cid, g.lower()) in _DEAD_OPT:           # label muerta -> base del grupo
        g = None
    y = kv.pop('year', None)                          # picker año (members con genre=año)
    entry = _GRP[cid].get(g)
    if entry is None:                                 # base: primer miembro VIVO
        entry = next((v for k, v in _GRP[cid].items()
                      if (cid, k.lower()) not in _DEAD_OPT),
                     next(iter(_GRP[cid].values())))
    utype, real, wants, forced = entry
    if wants:
        kv['genre'] = y or _YR_OPTS[cid][0]     # sin año: el más reciente
    if forced:
        kv['genre'] = forced                   # 'Label · sub' -> genre=sub upstream
    return utype, real, urllib.parse.urlencode(kv, safe=',')


_CAL_LOCK = threading.Lock()   # builds de calendario >10s: un build a la vez


def _synth_recom(utype, extra, _defx, _key):
    """recomendaciones trakt (cache en _cat_c: related es caro)."""
    hit2 = _cget(_cat_c, ('recom', utype))
    if hit2 is None:
        hit2 = _recom(utype)
        _cset(_cat_c, ('recom', utype), hit2)
    return hit2, utype, extra

def _synth_cal(utype, extra, _defx, key):
    """estrenos del calendario (extra=ids pedidos; respuesta cacheada con clave
    única — el extra se ignora y todas las variantes de ids comparten la build;
    sin compartir, cada set de ids de biblioteca repetía el build >10s y el
    cliente moría por timeout cada vez). Una build degradada (<20) no se cachea:
    así la siguiente petición reintenta en vez de servir vacío 10 min."""
    ck = ('estrenoscal', _cfgget('calendar_months_ahead', 2))   # meses varía por perfil
    hit2 = _cget(_cat_c, ck)
    if hit2 is None:
        with _CAL_LOCK:                                   # build >10s: nada de thundering-herd
            hit2 = _cget(_cat_c, ck)                      # doble-check: otro request la llenó
            if hit2 is None:
                hit2 = _estrenos_cal(extra)
                if hit2 and len(hit2.get('metasDetailed') or ()) >= 20:
                    _cset(_cat_c, ck, hit2)
    return hit2 or {'metas': []}, utype, extra

def _synth_m3u(extra):
    """M3U: sin extra = lista de canales; con date= = guía EPG de ese día."""
    kv = dict(urllib.parse.parse_qsl(extra or ''))
    day = kv.get('date')
    metas = _m3u_metas()
    if day:
        return epg_metas(day, metas)
    return {'metas': metas}


_SYNTH_H = {'_busqueda': lambda utype, extra, defx, key: (_busqueda(utype, extra), utype, extra),
            '_recom': _synth_recom,
            '_list': lambda utype, extra, defx, key: (_list_metas(defx, utype), utype, extra),
            '_m3u': lambda utype, extra, defx, key: (_synth_m3u(extra), utype, extra),
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


def _cat_fix_extra(base, utype, real, extra):
    """'genre' no declarado por el catálogo upstream -> se descarta.
    Stremio inyecta opciones sintéticas en el selector ('Top' en Discover de
    canales) que el upstream desconoce -> 0 metas -> 'Oops!'. Sin la opción
    declarada en su manifest, se sirve el catálogo base. Groups (_GRP) ya
    mapean su genre a catálogo real — esto solo aplica a cats directos."""
    kv = dict(urllib.parse.parse_qsl(extra or ''))
    g = kv.get('genre')
    if not g or not base:
        return extra
    i = next((j for j, s in enumerate(SOURCES) if s.base == base), None)
    if i is None or i >= len(manifests):
        return extra
    cat = next((c for c in (manifests[i] or {}).get('catalogs') or []
                if c.get('id') == real and c.get('type') == utype), None)
    opts = next((e.get('options') for e in (cat or {}).get('extra') or []
                 if e.get('name') == 'genre'), None)
    cid = f"s{SOURCES[i].name.lower()}_{utype}_{real}"
    live = {str(o).lower() for o in (opts or [])} - {v for c2, v in _DEAD_OPT if c2 == cid}
    if opts and g.lower() not in live:              # no declarada o muerta
        kv.pop('genre')
        return urllib.parse.urlencode(kv, safe=',')
    return extra


def _grp_failover(cid, base, real, kv, d):
    """Miembro del grupo caído (None) o vacío -> prueba los demás miembros
    en orden y sirve el primero con metas. 'Events' 503/[] no debe dejar el
    grupo entero en Oops! mientras otro miembro (DLStreams) tenga contenido."""
    if isinstance(d, dict) and (d.get('metas') or d.get('metasDetailed')):
        return d
    seen, tried = {real}, set()
    for _label, (ut, r, wants, forced) in _GRP[cid].items():
        if r in seen:
            continue
        seen.add(r)
        kv2 = dict(kv)
        kv2.pop('genre', None)                        # el genre era del miembro caído
        if wants:
            kv2['genre'] = _YR_OPTS[cid][0]
        if forced:
            kv2['genre'] = forced
        tail = urllib.parse.urlencode(kv2, safe=',')
        tail = f"/{urllib.parse.quote(tail, safe='=&,:')}" if tail else ''
        tried.add(r)
        d2 = fetch(f"{base}/catalog/{ut}/{urllib.parse.quote(r, safe='')}{tail}.json", 7)
        if isinstance(d2, dict) and (d2.get('metas') or d2.get('metasDetailed')):
            return d2
        if isinstance(d2, dict):                     # 200-vacío: último recurso
            d = d2
    return d


def _cat_norm(d):
    """Respuesta catálogo -> contrato: metas/metasDetailed solo listas de dicts."""
    for k in ('metas', 'metasDetailed'):
        if k in d:
            d[k] = [m for m in d[k] if isinstance(m, dict)] \
                if isinstance(d[k], list) else []


def handle_catalog(stype, cid, extra):
    """GET /catalog — routing: sintéticos, listas externas, rewrite anime y upstream."""
    key = (stype, cid, extra)
    req_kv = dict(urllib.parse.parse_qsl(extra or ''))
    req_g = req_kv.get('genre')                                     # label pedida
    req_only_g = req_g is not None and set(req_kv) == {'genre'}     # sin más filtros
    hit = _cget(_cat_c, key)
    if hit is not None:
        return _rpdb(hit)                                # rpdb es por perfil: fuera del caché
    try:
        resolved = _cat_resolve(cid, stype, extra, key)
    except (ValueError, StopIteration, IndexError):
        return {'metas': []}
    if isinstance(resolved, dict):                            # sintético respondió directo
        return _rpdb(resolved)
    base, utype, real, extra = resolved
    extra = _cat_fix_extra(base, utype, real, extra)
    tail = f"/{urllib.parse.quote(extra, safe='=&,:')}" if extra else ''
    d = fetch(f"{base}/catalog/{utype}/{urllib.parse.quote(real, safe='')}{tail}.json", 7)
    empty0 = not isinstance(d, dict) or not (d.get('metas') or d.get('metasDetailed'))
    if cid in _GRP and empty0:
        kv = dict(urllib.parse.parse_qsl(extra or ''))
        d = _grp_failover(cid, base, real, kv, d)
    if isinstance(d, dict):
        _cat_norm(d)
        empty = not (d.get('metas') or d.get('metasDetailed'))
        if empty0 and req_only_g:                    # su miembro/opción vino vacío
            _DEAD_OPT.add((cid, req_g.lower() if req_g else ''))   # label pedida
        if empty and not extra:                      # servido sin params = base muerto
            _DEAD_OPT.add((cid, ''))
        _fix_metas(d)
        if not empty:
            _cset(_cat_c, key, d)                    # vacío: no cachear (reintenta)
        return _rpdb(d)                              # no-destructivo -> hay que usarlo
    return _cstale(_cat_c, key) or {'metas': []}   # upstream caído: sirve stale
