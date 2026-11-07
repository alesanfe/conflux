"""GET /stream: colección paralela, dedup, debrid-mark, url-health."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import os
import re
import threading
import urllib.parse
from concurrent.futures import as_completed

from conflux.core import cfg, state as _st
from conflux.core.cfg import _eff_disabled_for, _es_pat, _pcfg, _strs
from conflux.core.state import (
    _POOL,
    _POOL_BG,
    _SERVED,
    COOLDOWN,
    COOLDOWN_T,
    FAIL_CD_T,
    FAIL_STREAK,
    HEALTH,
    SOURCES,
    STREAM_BUDGET,
    STREAM_TIMEOUT,
    _cache,
    _cget,
    _cset,
    _health,
    log_ev,
    manifests,
)
from conflux.domain.lists import _M3U, _m3u_metas
from conflux.domain.sources import _resolve_sid
from conflux.domain.text import RANK, _ad_link, _playable, _qscore, _skip_manifest, _spam, build_display, norm_stream
from conflux.infra.debrid import _debrid_mark
from conflux.infra.net import _session, fetch
from conflux.infra.easynews import _easynews
from conflux.infra.newznab import _newznab
from conflux.infra.scrape import _scraper, _scraper_names
from conflux.infra.torrent_health import _hash_health
from conflux.infra.torznab import _torznab
from conflux.infra.zilean import _zilean

_inflight: dict = {}            # (stype,sid) -> estado compartido

_slock    = threading.Lock()

_RE_LIMIT  = re.compile(r'daily limit|limit reached|link limit|quota', re.IGNORECASE)

def _collect_skip(i, stype, sid, dis):
    """Por qué NO consultar la fuente i (skip blando), o None si toca fetch.
    dis viene capturado del hilo del request: _PROF es thread-local y los
    workers del pool verían siempre el set global."""
    name = SOURCES[i].name
    if name in dis:
        return 'deshabilitada'
    m = manifests[i] if i < len(manifests) else None
    r = _skip_manifest(m, stype, sid)
    if r:
        return r
    cd = COOLDOWN.get(name)
    if cd and _st.CLOCK.time() < cd[0]:                                # cuota o fallos: saltar
        return f'en espera ({cd[1]})'
    return None


def _quota_hit(ss):
    """La respuesta lleva aviso de cuota/rate-limit (disyuntor 12h)."""
    return any(isinstance(s, dict) and _RE_LIMIT.search(str(s.get('title') or '')) for s in ss)


def _collect_one(i, stype, sid, dis):
    """Fetch de una fuente -> (nombre, streams, usable). Health + disyuntores."""
    name = SOURCES[i].name
    skip = _collect_skip(i, stype, sid, dis)
    if skip:
        if skip != 'deshabilitada':
            _health(name, True, skip, 0)
        return name, [], 0
    name, d, ss = _collect_fetch(i, stype, sid)
    if _quota_hit(ss):
        COOLDOWN[name] = (_st.CLOCK.time() + COOLDOWN_T, 'cuota')       # disyuntor 12h
        _health(name, True, 'cuota agotada → espera 12h', 0)
        return name, [], 0
    return name, ss, _collect_health(name, d, ss)

def _collect_health(name, d, ss):
    """Health + cooldown por racha de fallos (o nada si responde bien).
    -> nº usable: el _skip_stream del conteo ya corrió aquí, no se repite."""
    usable = sum(1 for s in ss if not _skip_stream(s))
    _health(name, d is not None, '' if d is not None else 'timeout/error', usable)
    if d is None and HEALTH[name]['streak'] >= FAIL_STREAK:        # racha de fallos: descanso
        COOLDOWN[name] = (_st.CLOCK.time() + FAIL_CD_T, 'fallos')
    return usable


def _collect_fetch(i, stype, sid):
    """El fetch real de la fuente (post-guards)."""
    name, base = SOURCES[i]
    d = fetch(f"{base}/stream/{stype}/{urllib.parse.quote(sid, safe=':')}.json",
              cfg.CFG.get('source_timeout') or STREAM_TIMEOUT)
    ss = d.get('streams') if isinstance(d, dict) else None
    if not isinstance(ss, list):
        ss = []            # "streams":null, tipo raro o JSON no-dict
    return name, d, ss


def _merge_key(s):
    """Clave dedup del stream: infoHash (canónica) o URL — minúsculas."""
    k = s.get('infoHash') or s.get('url') or s.get('externalUrl') or s.get('ytId')
    return str(k).lower() if k else None          # infoHash: Ytztvio usa MAYÚSCULAS


def _skip_stream(s):
    """Filtro de entrada unificado (merge + contrib): no-dict, spam, promo
    externalUrl-only o stream sin localizador reproducible (STM-001)."""
    return not isinstance(s, dict) or _spam(s) or _ad_link(s) or not _playable(s)


def _collect_merge(state, name, streams):
    """Merge dedup + norm de los streams de la fuente en state."""
    for s in streams:
        try:
            if _skip_stream(s):
                continue
            k = _merge_key(s)
            if not k or k in state['seen']:
                continue
            state['seen'].add(k)
            nr = norm_stream(s, name)
        except Exception:                             # noqa: BLE001 — un stream
            continue                                  # malformado no mata el batch
        state['rows'].append(nr)
        sv = _SERVED.setdefault(name, {'n': 0, 'res': {}})
        sv['n'] += 1                                    # 5.2: qué fuente aporta y en qué calidad
        sv['res'][nr[1] or 'SD'] = sv['res'].get(nr[1] or 'SD', 0) + 1


def _collect_tz(stype, sid):
    """Worker Torznab (indexer local): mismo contrato que _collect_one."""
    try:
        ss = _torznab(stype, sid)
    except Exception as e:                              # noqa: BLE001 — indexer local nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return 'Torznab', [], 0
    return 'Torznab', ss, sum(1 for s in ss if not _skip_stream(s))


def _collect_zl(stype, sid):
    """Worker Zilean (hashlists DMM ya cacheadas): mismo contrato que _collect_one."""
    try:
        ss = _zilean(stype, sid)
    except Exception as e:                              # noqa: BLE001 — indexer local nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return 'Zilean', [], 0
    return 'Zilean', ss, sum(1 for s in ss if not _skip_stream(s))


def _collect_nz(stype, sid):
    """Worker Newznab (usenet via TorBox): mismo contrato que _collect_one."""
    try:
        ss = _newznab(stype, sid)
    except Exception as e:                              # noqa: BLE001 — indexer nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return 'Newznab', [], 0
    return 'Newznab', ss, sum(1 for s in ss if not _skip_stream(s))


def _collect_en(stype, sid):
    """Worker Easynews (usenet directo): mismo contrato que _collect_one."""
    try:
        ss = _easynews(stype, sid)
    except Exception as e:                              # noqa: BLE001 — fuente nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return 'Easynews', [], 0
    return 'Easynews', ss, sum(1 for s in ss if not _skip_stream(s))


def _collect_sc(name, stype, sid):
    """Worker de scraper integrado (yts/eztv/nyaa): mismo contrato."""
    try:
        ss = _scraper(name, stype, sid)
    except Exception as e:                              # noqa: BLE001 — scraper nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return name, [], 0
    return name, ss, sum(1 for s in ss if not _skip_stream(s))


def _collect(key, state):
    stype, sid, dis = key                     # dis: sources_disabled efectivo (perfil)
    sid = _resolve_sid(stype, sid)            # kitsu/mal -> imdb para las fuentes
    t = None
    try:
        futs = [_POOL.submit(_collect_one, i, stype, sid, dis) for i in range(len(SOURCES))]
        futs.append(_POOL.submit(_collect_tz, stype, sid))     # indexer en paralelo: no como straggler en finally
        futs.append(_POOL.submit(_collect_zl, stype, sid))     # zilean (DMM cacheados): idem
        futs.append(_POOL.submit(_collect_nz, stype, sid))     # newznab (usenet/torbox): idem
        futs.append(_POOL.submit(_collect_en, stype, sid))     # easynews (usenet directo): idem
        futs += [_POOL.submit(_collect_sc, nm, stype, sid)     # scrapers integrados (yts/eztv/nyaa)
                 for nm in _scraper_names()]
        pending = len(futs)

        def _nearly_done():                                    # ~2s: si solo cuelgan stragglers
            if pending <= 3:                                   # (típicos muertos/cold-start),
                state['done'].set()                            # despertar: waiters ya tienen ~todo
        t = threading.Timer(2.0, _nearly_done)
        t.daemon = True
        t.start()
        for f in as_completed(futs):
            pending -= 1
            try:
                name, streams, usable = f.result()
            except Exception as e:                              # noqa: BLE001 — el worker puede lanzar cualquier cosa
                log_ev('src_worker', err=repr(e)[:100])
                continue
            state['contrib'][name] = usable                     # por collect: sin mezclas
            with state['lock']:
                _collect_merge(state, name, streams)
    finally:
        if t:                                                   # collect rápido: suelta el timer
            t.cancel()
        with state['lock']:
            pat = _pref_pat()                             # resuelto 1 vez: no por fila
            final = sorted(state['rows'],
                           key=lambda r: _rowkey(r, pat))  # filas completas: re-score por perfil
        with _slock:
            _cset(_cache, key, final)
            _inflight.pop(key, None)
        state['done'].set()          # despierta a handle_stream antes del budget
        log_ev('collect_done', sid=key[1], contrib=state['contrib'])


def _ensure_collect(key):
    with _slock:
        state = _inflight.get(key)
        if state is None:
            state = {'rows': [], 'seen': set(), 'contrib': {}, 'done': threading.Event(), 'lock': threading.Lock()}
            _inflight[key] = state
            threading.Thread(target=_collect, args=(key, state), daemon=True).start()
        return state


_STREAM_TEXT = ('name', 'title', 'description')


def _ex_out(streams):
    """Filtro stream_exclude (estilo AIOStreams): fuera lo que matchee el regex
    en name/title/description — p.ej. 'cam,screener,ts' para no ver rips de cine."""
    if not streams:
        return streams
    pat = cfg._ex_pat()                                # override de perfil si lo hay
    return [s for s in streams if not pat.search(
        ' '.join(str(s.get(k) or '') for k in _STREAM_TEXT))]


def _adult_out(streams):
    """parental (perfil/global): fuera streams con etiquetas adultas."""
    en = (_pcfg() or {}).get('parental', cfg.CFG.get('parental'))
    if not streams or not en:
        return streams
    return [s for s in streams if not cfg.RE_ADULT.search(
        ' '.join(str(s.get(k) or '') for k in _STREAM_TEXT))]


_REX_C: dict = {}                       # (key, tuple(pats)) -> [compiled]


def _rex_pats(key):
    """stream_*_regex (perfil/global) -> compilados cacheados. Los patrones
    rotos se registran y se saltan — un regex malo del usuario no rompe el
    stream (comportamiento AIOStreams: avisar, no morir)."""
    pats = _strs((_pcfg() or {}).get(key))
    if pats is None:
        pats = _strs(cfg.CFG.get(key))
    if not pats:
        return []
    ck = (key, tuple(pats))
    out = _REX_C.get(ck)
    if out is None:
        out = []
        for p in pats:
            try:
                out.append(re.compile(p))
            except re.error as e:
                log_ev('regex_bad', key=key, pat=p[:60], err=str(e)[:80])
        _REX_C[ck] = out
    return out


def _rex_text(s):
    return ' '.join(str(s.get(k) or '') for k in _STREAM_TEXT)


def _rex_ex_out(streams):
    """stream_exclude_regex: fuera lo que matchee CUALQUIER patrón."""
    pats = _rex_pats('stream_exclude_regex')
    if not streams or not pats:
        return streams
    return [s for s in streams if not any(p.search(_rex_text(s)) for p in pats)]


def _rex_req_out(streams):
    """stream_required_regex: solo pasa lo que matchee ALGÚN patrón."""
    pats = _rex_pats('stream_required_regex')
    if not streams or not pats:
        return streams
    return [s for s in streams if any(p.search(_rex_text(s)) for p in pats)]


def _req_out(streams):
    """Filtro stream_required: solo sobrevive lo que matchee ALGÚN término
    (required de AIOStreams — p.ej. ['es','latino'] = solo audio ES)."""
    terms = _strs((_pcfg() or {}).get('stream_required'))
    if terms is None:
        terms = cfg.CFG.get('stream_required')
    if not streams or not terms:
        return streams
    pat = cfg._ex_re(terms)                            # mismas reglas: <=3 borde, >=4 substring
    return [s for s in streams if pat.search(
        ' '.join(str(s.get(k) or '') for k in _STREAM_TEXT))]


def _user_filters(streams):
    """Cadena exclude→adult→required con el texto por stream calculado UNA vez:
    los 5 filtros por separado re-construían el join name+title+description en
    cada pase (5 joins+búsquedas por fila). Si ningún filtro está activo no
    construye ni un texto."""
    if not streams:
        return streams
    ex = cfg._ex_pat()
    ad = cfg.RE_ADULT \
        if ((_pcfg() or {}).get('parental', cfg.CFG.get('parental'))) else None
    rex_ex = _rex_pats('stream_exclude_regex')
    rex_req = _rex_pats('stream_required_regex')
    terms = _strs((_pcfg() or {}).get('stream_required'))
    if terms is None:
        terms = cfg.CFG.get('stream_required')
    req = cfg._ex_re(terms) if terms else None
    ex_on = ex.pattern != '(?!)'                         # '(?!)' = filtro vacío (_ex_re/RE_EX)
    if not ex_on and ad is None and not rex_ex and not rex_req and req is None:
        return streams                                   # sin filtros activos: 0 regex, 0 joins
    out = []
    for s in streams:
        t = _rex_text(s)
        if (ex_on and ex.search(t)) or (ad is not None and ad.search(t)) \
                or any(p.search(t) for p in rex_ex):
            continue                                     # excluded: término, adulto o regex
        if (req is not None and not req.search(t)) \
                or (rex_req and not any(p.search(t) for p in rex_req)):
            continue                                     # required: no matchea -> fuera
        out.append(s)
    return out


def _pref_pat():
    """stream_preferred -> patrón compilado (o None). Resolución UNA vez por
    request/sort — hacerlo por fila re-leía config y re-escapaba términos.
    Con términos no-vacíos el compile va memoizado (_ES_PAT_C) — con override
    de perfil cada request re-compilaba el mismo patrón."""
    terms = _strs((_pcfg() or {}).get('stream_preferred'))
    if terms is None:
        terms = cfg.CFG.get('stream_preferred')
    if not terms:
        return None
    k = ('pref',) + tuple(terms)
    p = cfg._ES_PAT_C.get(k)
    if p is None:
        if len(cfg._ES_PAT_C) > 64:
            cfg._ES_PAT_C.clear()
        p = cfg._ex_re(terms)
        cfg._ES_PAT_C[k] = p
    return p


def _pref_text(out):
    """Texto del stream sobre el que busca stream_preferred."""
    return ' '.join(str(out.get(k) or '') for k in _STREAM_TEXT)


def _seed_score(r, pat=None):
    """seeds crudos + boosts de preferencia: idioma ES (+500) y
    stream_preferred (+300 si matchea el título)."""
    if pat is None:
        pat = _pref_pat()
    return r[2] + 500 * (1 if r[5]['es'] else 0) + _pref_hit(r[0], pat)


def _pref_hit(out, pat):
    """+300 si el texto del stream matchea el patrón preferred ya compilado."""
    return 300 if pat is not None and pat.search(_pref_text(out)) else 0


def _pref_boost(out):
    """stream_preferred: boost (no filtro) — tercera pata de exclude/required/
    preferred de AIOStreams: 'remux,es' sube esos releases sin matar el resto."""
    return _pref_hit(out, _pref_pat())


_LIGHT_GB = 15.0   # <=15GB = stream "ligero" (STR-001): cabe en conexiones normales


def _size_band(sz):
    """Penalización de tamaño (STR-002) por bandas — un REMUX de 80GB no gana a
    un WEB-DL de 8GB solo por 'más bytes = más bitrate'. Dentro de la misma
    banda sigue ganando el mayor (proxy de bitrate); tamaño desconocido = 0.
    Sort ascendente -> penalización positiva: 0 ligero/desconocido, 1 medio,
    3 enorme."""
    if not sz:
        return 0
    if sz <= _LIGHT_GB:
        return 0
    if sz <= 40:
        return 1
    return 3


_SORT_FNS = {'res': lambda r: -RANK.get(r[1], 0), 'cached': lambda r: -r[4],
             'quality': lambda r: -r[5],
             'seeds': lambda r: -(r[2] + _pref_boost(r[0])),   # ES ya va en r[2] (lang_rows)
             'size': lambda r: (_size_band(r[3]), -r[3])}
_SORT_DEF = ('res', 'cached', 'quality', 'seeds', 'size')


def _sort_order():
    """stream_sort efectivo (perfil/global): criterios whitelistados; los no
    mencionados van detrás en orden por defecto. Vacío/raro -> default."""
    ov = _strs((_pcfg() or {}).get('stream_sort'))
    if ov is None:
        ov = _strs(cfg.CFG.get('stream_sort'))
    if not ov:
        return _SORT_DEF
    order = [x for x in ov if x in _SORT_FNS]
    return tuple(order + [x for x in _SORT_DEF if x not in order]) or _SORT_DEF


def _rowkey(r, pat=None):
    """Orden: resolución > debrid-cached > calidad(rip/hdr/codec/audio) >
    seeds(con boost es del global) > size (bandas: ligero > medio > enorme)."""
    if pat is None:
        pat = _pref_pat()
    return (-RANK.get(r[1], 0), -r[4], -_qscore(r[5] if len(r) > 5 else {}),
            -_seed_score(r, pat), (_size_band(r[3]), -r[3]))


def _lang_rows(rows):
    """rows -> (out,res,seeds,size,cached,qscore) con el patrón de idioma
    EFECTIVO del request: un perfil con stream_langs distinto re-puntúa y
    re-etiqueta la entrada cacheada (el flag/seeds horneados son del global)."""
    pc = _pcfg()
    ovr = _strs(pc.get('stream_langs')) if isinstance(pc, dict) else None
    if not ovr:
        return [(r[0], r[1], r[2] + (500 if r[5]['es'] else 0), r[3], r[4], _qscore(r[5]))
                if isinstance(r, tuple) else (r, 'SD', 0, 0, 0, 0) for r in rows]
    pat = _es_pat()
    rr = []
    for r in rows:
        if isinstance(r, dict):                        # entrada legacy: dict ya formateado
            rr.append((r, 'SD', 0, 0, 0, 0))
            continue
        out, res, seeds, size, cached, f, short = r
        es = bool(pat.search(f.get('text', '')))
        if es != f['es']:
            f = dict(f)
            f['es'] = es
            out = dict(out)
            out['name'], out['title'] = build_display(f, short)
        rr.append((out, res, seeds + (500 if es else 0), size, cached, _qscore(f)))
    return rr


def _light_pass(rr):
    """Pase ligero (STR-001): si el top-3 visible son solo archivos >15GB, el
    mejor stream ligero conocido sube al puesto 2 — el usuario ve una opción
    viable para conexiones limitadas sin perder el #1 de máxima calidad.
    Tamaño desconocido no cuenta como 'ligero' ni como 'pesado'.
    `stream_light: false` en config desactiva el pase."""
    if not cfg._cfgget('stream_light', True) or len(rr) < 3:
        return rr
    if not all(r[3] and r[3] > _LIGHT_GB for r in rr[:3]):
        return rr                                # ya hay ligero o desconocido arriba
    i = next((i for i, r in enumerate(rr) if r[3] and r[3] <= _LIGHT_GB), None)
    if i is None:
        return rr
    return rr[:1] + [rr[i]] + rr[1:i] + rr[i + 1:]


def _rows_out(rows):
    rr = _lang_rows(rows)
    mx = cfg._cfgget('stream_max_gb', 0) or 0              # 0 = sin límite; policy knob
    if mx:                                               # (un 4K de 80GB no siempre gana)
        rr = [r for r in rr if not r[3] or r[3] <= mx]   # size desconocido -> no castigar
    order = _sort_order()
    pat = _pref_pat()                                    # 1 vez por sort: antes por fila
    if order == _SORT_DEF:                               # caso común: key plana, sin dispatch
        rr.sort(key=lambda r: (-RANK.get(r[1], 0), -r[4], -r[5],
                               -(r[2] + _pref_hit(r[0], pat)),
                               (_size_band(r[3]), -r[3])))
    else:
        rr.sort(key=lambda r: tuple(
            -(r[2] + _pref_hit(r[0], pat)) if o == 'seeds' else _SORT_FNS[o](r)
            for o in order))
    return [r[0] for r in _light_pass(rr)]


_OUT_C: dict = {}          # (key, sig) -> out filtrado+ordenado (pre-debrid)
_OUT_CAP = 200


def _sig_list(k):
    """Knob lista efectivo (perfil > global) como tupla para la firma."""
    v = _strs((_pcfg() or {}).get(k))
    if v is None:
        v = _strs(cfg.CFG.get(k))
    return tuple(v or ())


def _pipe_sig():
    """Firma de TODOS los knobs que afectan al pipeline post-collect
    (_lang_rows/_rows_out/_user_filters/_light_pass). MISMA salida si la
    firma coincide; un knob nuevo en el pipeline debe entrar aquí o los
    requests servirían resultados con la config anterior (stale)."""
    pc = _pcfg() or {}
    return (
        _sig_list('stream_exclude'), _sig_list('stream_exclude_regex'),
        _sig_list('stream_required'), _sig_list('stream_required_regex'),
        _sig_list('stream_preferred'), _sig_list('stream_sort'),
        tuple(_strs(pc.get('stream_langs')) or ()),       # langs: solo perfil
        bool((_pcfg() or {}).get('parental', cfg.CFG.get('parental'))),
        cfg._cfgget('stream_max_gb', 0) or 0,
        bool(cfg._cfgget('stream_light', True)),
    )


def _rows_pipe(rows, key):
    """_rows_out + _user_filters memoizados por (key, firma de config):
    el path warm recomponía el pipeline entero por request (~57ms a 2k
    filas con filtros); con firma igual es un hit de dict.
    Solo para rows FINALES (caché): el entry retiene la referencia a rows
    y verifica identidad — un collect nuevo (TTL) produce otro objeto ->
    miss -> recomputa (nunca stale por id() reciclado). Devuelve COPIA de
    la lista — _debrid_mark/_url_health mutan slots y contaminarían el
    caché compartido entre perfiles."""
    k = (key, _pipe_sig())
    ent = _OUT_C.get(k)
    if ent is not None and ent[1] is rows:
        return list(ent[0])
    out = _user_filters(_rows_out(rows))
    if len(_OUT_C) >= _OUT_CAP:            # acotado: ids×firmas; clear simple
        _OUT_C.clear()                     # (LRU fino no compensa a 200 entradas)
    _OUT_C[k] = (out, rows)
    return list(out)


def handle_stream(stype, sid):
    """GET /stream — agrega fuentes (pool), dedup, ordena, marca debrid y filtra urls muertas."""
    if sid.startswith('m3u:'):                              # canales M3U propios
        if not _M3U:
            _m3u_metas()
        u = _M3U.get(sid)
        return {'streams': [{'name': 'M3U', 'title': 'Canal propio', 'url': u}] if u else []}
    key = (stype, sid, tuple(sorted(_eff_disabled_for(stype))))  # off+groups por perfil
    hit = _cget(_cache, key)
    if hit is not None:
        out = _rows_pipe(hit, key)
        _debrid_mark(out)                          # debrid marca copias: fuera del caché
        _url_health(out)                           # veredicto muerto filtra también el caché
        _hash_health(out)                          # veredictos scrape: filtran también el caché
        return {'streams': out}
    state = _ensure_collect(key)
    state['done'].wait(STREAM_BUDGET)
    hit = _cget(_cache, key)
    if hit is not None:
        # collect terminado: rows finales ya cacheados — la pipe memoiza y
        # TODOS los waiters comparten filtrado+sort en vez de re-ejecutarlo.
        out = _rows_pipe(hit, key)
    else:
        with state['lock']:
            # early-done: rows parciales en vuelo — no memoizar (siguen
            # llegando stragglers; el collect las ordenará al finalizar).
            out = _user_filters(_rows_out(state['rows']))            # filtros usuario tras dedup/rank
    _debrid_mark(out)                                        # si hay key: cache + resolve /r/
    _url_health(out)                                         # HEAD async a streams http
    _hash_health(out)                                        # infoHash sin swarm ni hints fuera
    return {'streams': out}



# ---- health-check de streams HTTP (3.4): HEAD async, muertos filtrados ----
_URL_BAD: dict = {}        # url -> ts marcado muerto (reintento a los 30min)

_URL_OK: dict = {}        # url -> ts verificado vivo

_URLH_PATH = os.path.join(_st._ROOT, '.url-health.json')   # veredictos persistidos


def _urlh_save():
    """Veredictos -> disco atómico (META-001): una url muerta no vuelve a
    servirse ni a costar otro HEAD en el arranque siguiente."""
    _st.health_save(_URLH_PATH, _URL_BAD, _URL_OK)

def _url_todo(streams, now):
    """urls externas http sin veredicto reciente -> candidatas a HEAD (máx 15).
    Las marcadas malas también reentran pasados 30min: un timeout transitorio
    o un 4xx de CDN caduco no condenan la url para siempre."""
    return [s['url'] for s in streams
            if s.get('url', '').startswith('http') and '127.0.0.1' not in s['url']
            and now - _URL_BAD.get(s['url'], (0, 0))[1] > 1800
            and now - _URL_OK.get(s['url'], 0) > 1800][:15]


_URL_PEND: set = set()      # HEADs en vuelo — sin dedup cada request re-encola
                            # las mismas 15 urls mientras el probe anterior corre


def _url_head(u):
    """HEAD async: <400 -> vivo; 4xx/5xx -> muerte segura; resto -> 1 strike."""
    try:
        r = _session(urllib.parse.urlsplit(u).netloc).head(u, timeout=3, allow_redirects=True)
        if r.status_code < 400:
            _URL_OK[u] = _st.CLOCK.time()
            _URL_BAD.pop(u, None)
        else:
            _URL_BAD[u] = (2, _st.CLOCK.time())                 # 4xx/5xx: muerte segura
    except Exception:                                    # noqa: BLE001 — timeout/SSL/cualquiera = strike
        st, _ = _URL_BAD.get(u, (0, 0))
        _URL_BAD[u] = (st + 1, _st.CLOCK.time())                # timeout: strike
    _URL_PEND.discard(u)
    _urlh_save()


def _url_prune():
    """Cota de memoria de los veredictos: purga la mitad más vieja de cada dict."""
    for u in sorted(_URL_BAD, key=lambda u: _URL_BAD[u][1])[:len(_URL_BAD) // 2]:
        _URL_BAD.pop(u, None)
    for u in sorted(_URL_OK, key=lambda u: _URL_OK[u])[:len(_URL_OK) // 2]:
        _URL_OK.pop(u, None)


def _url_health(streams):
    """Streams http: los confirmados muertos salen de la lista; los desconocidos
    se HEAD-encolan async (máx 15, 3s). Fallo transitorio (timeout) solo cuenta
    medio strike — 2 strikes o un 4xx/5xx confirman muerte."""
    now = _st.CLOCK.time()
    if len(_URL_BAD) + len(_URL_OK) > 2000:
        _url_prune()
    # Un pase: veredicto BAD resuelto una vez por url — antes _url_todo + filtro
    # lo consultaban dos veces. Los muertos vigentes no entran en todo (su ts
    # <1800 ya los excluye del criterio de antigüedad). `_URL_PEND` evita
    # re-encolar HEADs ya en vuelo (requests consecutivos con las mismas urls).
    out: list = []
    todo: list = []
    empty_bad = not _URL_BAD                  # sin malos: tras llenar el cupo
    for i, s in enumerate(streams):           # de probes el resto no filtra nada
        u = s.get('url')
        if isinstance(u, str) and u.startswith('http') and '127.0.0.1' not in u:
            v = _URL_BAD.get(u)
            if v is not None and v[0] >= 2 and now - v[1] < 1800:
                continue                      # muerto confirmado y vigente: fuera
            if len(todo) < 15 and u not in _URL_PEND \
                    and (v is None or now - v[1] > 1800) \
                    and now - _URL_OK.get(u, 0) > 1800:
                todo.append(u)                # sin veredicto fresco: HEAD async
                _URL_PEND.add(u)
                if empty_bad and len(todo) == 15:
                    out.extend(streams[i + 1:])
                    break
        out.append(s)
    streams[:] = out
    for u in todo:
        try:
            _POOL_BG.submit(_url_head, u)
        except Exception:                                 # noqa: BLE001 — pool cerrado
            _URL_PEND.discard(u)


_st.health_load(_URLH_PATH, _URL_BAD, _URL_OK)   # veredictos del proceso anterior
