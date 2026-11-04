"""GET /stream: colección paralela, dedup, debrid-mark, url-health."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import re
import threading
import time
import urllib.parse
from concurrent.futures import as_completed

from conflux.core import cfg
from conflux.core.cfg import _eff_disabled, _es_pat, _pcfg, _strs
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
from conflux.domain.text import RANK, _qscore, _skip_manifest, _spam, build_display, norm_stream
from conflux.infra.debrid import _debrid_mark
from conflux.infra.net import _session, fetch
from conflux.infra.easynews import _easynews
from conflux.infra.newznab import _newznab
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
    if cd and time.time() < cd[0]:                                # cuota o fallos: saltar
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
        COOLDOWN[name] = (time.time() + COOLDOWN_T, 'cuota')       # disyuntor 12h
        _health(name, True, 'cuota agotada → espera 12h', 0)
        return name, [], 0
    _collect_health(name, d, ss)
    return name, ss, sum(1 for s in ss if isinstance(s, dict) and not _spam(s))

def _collect_health(name, d, ss):
    """Health + cooldown por racha de fallos (o nada si responde bien)."""
    usable = sum(1 for s in ss if isinstance(s, dict) and not _spam(s))
    _health(name, d is not None, '' if d is not None else 'timeout/error', usable)
    if d is None and HEALTH[name]['streak'] >= FAIL_STREAK:        # racha de fallos: descanso
        COOLDOWN[name] = (time.time() + FAIL_CD_T, 'fallos')


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


def _collect_merge(state, name, streams):
    """Merge dedup + norm de los streams de la fuente en state."""
    for s in streams:
        if not isinstance(s, dict) or _spam(s):
            continue
        k = _merge_key(s)
        if not k or k in state['seen']:
            continue
        state['seen'].add(k)
        nr = norm_stream(s, name)
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
    return 'Torznab', ss, sum(1 for s in ss if isinstance(s, dict) and not _spam(s))


def _collect_zl(stype, sid):
    """Worker Zilean (hashlists DMM ya cacheadas): mismo contrato que _collect_one."""
    try:
        ss = _zilean(stype, sid)
    except Exception as e:                              # noqa: BLE001 — indexer local nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return 'Zilean', [], 0
    return 'Zilean', ss, sum(1 for s in ss if isinstance(s, dict) and not _spam(s))


def _collect_nz(stype, sid):
    """Worker Newznab (usenet via TorBox): mismo contrato que _collect_one."""
    try:
        ss = _newznab(stype, sid)
    except Exception as e:                              # noqa: BLE001 — indexer nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return 'Newznab', [], 0
    return 'Newznab', ss, sum(1 for s in ss if isinstance(s, dict) and not _spam(s))


def _collect_en(stype, sid):
    """Worker Easynews (usenet directo): mismo contrato que _collect_one."""
    try:
        ss = _easynews(stype, sid)
    except Exception as e:                              # noqa: BLE001 — fuente nunca tumba el collect
        log_ev('src_worker', err=repr(e)[:100])
        return 'Easynews', [], 0
    return 'Easynews', ss, sum(1 for s in ss if isinstance(s, dict) and not _spam(s))


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
            final = sorted(state['rows'], key=_rowkey)      # filas completas: re-score por perfil
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


def _seed_score(r):
    """seeds crudos + boosts de preferencia: idioma ES (+500) y
    stream_preferred (+300 si matchea el título)."""
    return r[2] + 500 * (1 if r[5]['es'] else 0) + _pref_boost(r[0])


def _pref_boost(out):
    """stream_preferred: boost (no filtro) — tercera pata de exclude/required/
    preferred de AIOStreams: 'remux,es' sube esos releases sin matar el resto."""
    terms = _strs((_pcfg() or {}).get('stream_preferred'))
    if terms is None:
        terms = cfg.CFG.get('stream_preferred')
    if not terms:
        return 0
    return 300 if cfg._ex_re(terms).search(
        ' '.join(str(out.get(k) or '') for k in _STREAM_TEXT)) else 0


_SORT_FNS = {'res': lambda r: -RANK.get(r[1], 0), 'cached': lambda r: -r[4],
             'quality': lambda r: -r[5],
             'seeds': lambda r: -(r[2] + _pref_boost(r[0])),   # ES ya va en r[2] (lang_rows)
             'size': lambda r: -r[3]}
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


def _rowkey(r):
    """Orden: resolución > debrid-cached > calidad(rip/hdr/codec/audio) >
    seeds(con boost es del global) > size."""
    return (-RANK.get(r[1], 0), -r[4], -_qscore(r[5] if len(r) > 5 else {}),
            -_seed_score(r), -r[3])


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
        es = bool(pat.search(f['text']))
        if es != f['es']:
            f = dict(f)
            f['es'] = es
            out = dict(out)
            out['name'], out['title'] = build_display(f, short)
        rr.append((out, res, seeds + (500 if es else 0), size, cached, _qscore(f)))
    return rr


def _rows_out(rows):
    rr = _lang_rows(rows)
    mx = cfg._cfgget('stream_max_gb', 0) or 0              # 0 = sin límite; policy knob
    if mx:                                               # (un 4K de 80GB no siempre gana)
        rr = [r for r in rr if not r[3] or r[3] <= mx]   # size desconocido -> no castigar
    order = _sort_order()
    rr.sort(key=lambda r: tuple(_SORT_FNS[o](r) for o in order))
    return [r[0] for r in rr]


def handle_stream(stype, sid):
    """GET /stream — agrega fuentes (pool), dedup, ordena, marca debrid y filtra urls muertas."""
    if sid.startswith('m3u:'):                              # canales M3U propios
        if not _M3U:
            _m3u_metas()
        u = _M3U.get(sid)
        return {'streams': [{'name': 'M3U', 'title': 'Canal propio', 'url': u}] if u else []}
    key = (stype, sid, tuple(sorted(_eff_disabled())))      # fuentes off varía por perfil
    hit = _cget(_cache, key)
    if hit is not None:
        out = _adult_out(_req_out(_ex_out(_rows_out(hit))))
        _debrid_mark(out)                          # debrid marca copias: fuera del caché
        _hash_health(out)                          # veredictos scrape: filtran también el caché
        return {'streams': out}
    state = _ensure_collect(key)
    state['done'].wait(STREAM_BUDGET)
    with state['lock']:
        out = _rows_out(state['rows'])
    out = _adult_out(_req_out(_ex_out(out)))                 # filtros usuario tras dedup/rank
    _debrid_mark(out)                                        # si hay key: cache + resolve /r/
    _url_health(out)                                         # HEAD async a streams http
    _hash_health(out)                                        # infoHash sin swarm ni hints fuera
    return {'streams': out}



# ---- health-check de streams HTTP (3.4): HEAD async, muertos filtrados ----
_URL_BAD: dict = {}        # url -> ts marcado muerto (reintento a los 30min)

_URL_OK: dict = {}        # url -> ts verificado vivo

def _url_todo(streams, now):
    """urls externas http sin veredicto reciente -> candidatas a HEAD (máx 15).
    Las marcadas malas también reentran pasados 30min: un timeout transitorio
    o un 4xx de CDN caduco no condenan la url para siempre."""
    return [s['url'] for s in streams
            if s.get('url', '').startswith('http') and '127.0.0.1' not in s['url']
            and now - _URL_BAD.get(s['url'], (0, 0))[1] > 1800
            and now - _URL_OK.get(s['url'], 0) > 1800][:15]


def _url_head(u):
    """HEAD async: <400 -> vivo; 4xx/5xx -> muerte segura; resto -> 1 strike."""
    try:
        r = _session(urllib.parse.urlsplit(u).netloc).head(u, timeout=3, allow_redirects=True)
        if r.status_code < 400:
            _URL_OK[u] = time.time()
            _URL_BAD.pop(u, None)
        else:
            _URL_BAD[u] = (2, time.time())                 # 4xx/5xx: muerte segura
    except Exception:                                    # noqa: BLE001 — timeout/SSL/cualquiera = strike
        st, _ = _URL_BAD.get(u, (0, 0))
        _URL_BAD[u] = (st + 1, time.time())                # timeout: strike


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
    now = time.time()
    if len(_URL_BAD) + len(_URL_OK) > 2000:
        _url_prune()
    todo = _url_todo(streams, now)          # antes de filtrar: los muertos también
    dead = {u for u, (st, ts) in _URL_BAD.items() if st >= 2 and now - ts < 1800}
    if dead:                                # reintentan su resurrección al HEAD
        streams[:] = [s for s in streams if s.get('url') not in dead]
    for u in todo:
        _POOL_BG.submit(_url_head, u)
