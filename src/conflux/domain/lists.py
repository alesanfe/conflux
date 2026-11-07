"""Catálogos externos (IMDb/MDBList) y M3U local."""
# pylint: disable=broad-exception-caught,line-too-long

# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import os
import re
import urllib.parse
from contextlib import suppress

import requests

from conflux.core import state as _st
from conflux.core.state import (
    _POOL_BG,
    CFG,
    SOURCES,
    CLOCK,
    _cat_c,
    _cget,
    _cset,
    _open,
    as_dict,
    log_ev,
)
from conflux.infra.net import _session, fetch

_LLIST: dict = {}

def _list_defs():
    """config.catalog_lists: URLs (IMDb/MDBList) separadas por coma."""
    return [x.strip() for x in (CFG.get('catalog_lists') or '').split(',') if x.strip()]


def _list_imdb(url):
    m = re.search(r'/list/(ls\d+)', url)
    if not m:
        raise ValueError('url imdb no reconocida')
    r = _session('www.imdb.com').get(f'https://www.imdb.com/list/{m[1]}/export', timeout=10)
    items = [('series' if 'series' in c[9].lower() else 'movie', c[1])
             for row in r.text.splitlines()[1:201]
             if len(c := row.split(',')) > 9 and c[1].startswith('tt')]
    return f'IMDb · {m[1]}', items


def _list_mdblist(url):
    m = re.search(r'mdblist\.com/lists/(\d+)|^mdblist:(\d+)$', url)
    lid = (m.group(1) or m.group(2)) if m else None
    key = CFG.get('mdblist_key', '')
    if not lid or not key:
        raise ValueError('mdblist sin id o key')
    d = _session('api.mdblist.com').get(
        f'https://api.mdblist.com/lists/{lid}/items?apikey={key}&limit=200', timeout=10).json()
    return d.get('name') or f'MDBList {lid}', _mdblist_items(d)


def _mdblist_items(d):
    """items mdblist -> [(type, tt_id)]."""
    return [('series' if it.get('mediatype') == 'show' else 'movie',
             'tt' + str(it['imdb_id']).lstrip('t'))
            for it in (d.get('items') or []) if it.get('imdb_id')]


def _list_fetch(url):
    """Lista externa -> (nombre, [(type, tt_id)])  — cacheada en _cat_c."""
    hit = _cget(_cat_c, ('list', url))
    if hit is not None:
        return hit
    name, items = 'Lista', []
    with suppress(requests.RequestException, TypeError, ValueError, KeyError, AttributeError):
        if 'trakt.tv' in url:
            raise ValueError('listas Trakt retiradas — usa IMDb o MDBList')
        if 'imdb.com' in url:
            name, items = _list_imdb(url)
        else:
            name, items = _list_mdblist(url)
    res = (name, items)
    _cset(_cat_c, ('list', url), res)
    return res


def _list_metas(url, utype):
    """Items de lista -> metas (resueltos via Cinemeta por tt). El resultado
    compuesto también va a caché: sin ella cada vista de la lista refetcheaba
    N metas upstream aunque la lista no hubiera cambiado."""
    ck = ('listmetas', url, utype)
    hit = _cget(_cat_c, ck)
    if hit is not None:
        return hit
    _, items = _list_fetch(url)
    ids = [i for t, i in items if t == utype]
    cine = next(b for n, b in SOURCES if n == 'Cinemeta')
    out = []
    for f in _POOL_BG.map(lambda i: fetch(f"{cine}/meta/{utype}/{i}.json", 8), ids):
        m = as_dict(f).get('meta')
        if m:
            out.append(m)
    res = {'metas': out}
    if out:                                          # vacío/degradado: no fijar
        _cset(_cat_c, ck, res)
    return res



_M3U: dict = {}

_M3U_METAS: list = []
_M3U_TXT = [None]        # contenido ya parseado — no re-parsear por request
_M3U_SIG: list = [None]  # (path, mtime_ns, size) — stat() ~50us vs read() MBs


def _m3u_metas():
    """Parsea config.m3u_path -> metas tv + streams indexados (m3u:N).
    stat() primero (barato): mtime/size iguales -> caché sin leer el fichero
    (antes releía MBs por request). stat falla -> se intenta leer igual
    (tests con _open mockeado / fs sin stat)."""
    try:
        st_ = os.stat(CFG['m3u_path'])
        sig = (CFG['m3u_path'], st_.st_mtime_ns, st_.st_size)
        if sig == _M3U_SIG[0]:
            return _m3u_out([{**m} for m in _M3U_METAS])
    except OSError:
        sig = None
    try:
        txt = _open(CFG['m3u_path'], encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    if sig is not None:
        _M3U_SIG[0] = sig
    if txt == _M3U_TXT[0]:
        return _m3u_out([{**m} for m in _M3U_METAS])
    metas: list = []
    idx: dict = {}                         # local + swap: un reader durante el
    cur = {'name': '', 'logo': '', 'group': '', 'tvg': ''}   # re-parseo no ve el dict a medias
    for raw in txt.splitlines():
        ln = raw.strip()
        if ln.startswith('#EXTINF'):
            cur = _m3u_extinf(ln)
        elif ln and not ln.startswith('#'):
            cid = f'm3u:{len(metas) + 1}'
            idx[cid] = ln
            metas.append({'id': cid, 'type': 'tv', 'name': cur['name'],
                          'poster': cur['logo'] or 'https://i.imgur.com/tTCuczU.png',
                          'posterShape': 'square',
                          'description': cur['group'],
                          'tvg': cur['tvg'],
                          'behaviorHints': {'isLive': True}})
            cur = {'name': '', 'logo': '', 'group': '', 'tvg': ''}
    _M3U.clear()
    _M3U.update(idx)
    _M3U_TXT[0] = txt
    _M3U_METAS[:] = metas
    _iptv_sweep()                                   # canales nuevos: verificar en bg
    return _m3u_out(metas)


# ---- cuarentena de canales IPTV muertos (IPTV-001/002) ----
_IPTV_BAD: dict = {}        # url -> (strikes, ts) — mismo contrato que _URL_BAD
_IPTV_OK: dict = {}         # url -> ts última prueba viva
_IPTV_PEND: set = set()     # probes en vuelo (dedupe + cap)
_IPTV_Q_T = 6 * 3600        # cuarentena: 6h oculto; el barrido re-prueba a 3h
_IPTV_MAX_PEND = 24         # m3u8 ~4s timeout: cap para no fundir _POOL_BG
_IPTVH_PATH = os.path.join(_st._ROOT, '.iptv-health.json')


def _iptv_probe(u):
    """GET streaming (HEAD suele dar 405 en servidores m3u8): <400 vivo;
    4xx/5xx muerto confirmado; timeout/conexión -> strike."""
    try:
        r = _session(urllib.parse.urlsplit(u).netloc).get(u, timeout=4, stream=True)
        try:
            ok = r.status_code < 400
        finally:
            r.close()
        if ok:
            _IPTV_OK[u] = CLOCK.time()
            _IPTV_BAD.pop(u, None)
        else:
            _IPTV_BAD[u] = (2, CLOCK.time())
    except Exception:                                    # noqa: BLE001
        st, _ = _IPTV_BAD.get(u, (0, 0))
        _IPTV_BAD[u] = (st + 1, CLOCK.time())
    _st.health_save(_IPTVH_PATH, _IPTV_BAD, _IPTV_OK, keep=_IPTV_Q_T)
    _IPTV_PEND.discard(u)


def _iptv_dead(u):
    """Canal en cuarentena: muerto confirmado hace <6h (después se re-prueba)."""
    st, ts = _IPTV_BAD.get(u, (0, 0))
    return st >= 2 and CLOCK.time() - ts < _IPTV_Q_T


def _iptv_due(u, now):
    """¿Toca probe? cuarentena re-prueba a media ventana (recuperación);
    strike suelto a 30min; vivos se re-verifican a 6h."""
    st, bts = _IPTV_BAD.get(u, (0, 0))
    if st >= 2:
        return now - bts > _IPTV_Q_T // 2
    return (now - bts > 1800 and now - _IPTV_OK.get(u, 0) > _IPTV_Q_T)


def _iptv_sweep():
    """Barrido proactivo (IPTV-001/002): encola probes de canales sin
    veredicto fresco. Cap de pendientes: un .m3u de 500 canales no dispara
    500 sockets a la vez — el resto entra en el siguiente barrido (30min).
    `iptv_health: false` en config lo desactiva."""
    if not _M3U or CFG.get('iptv_health', True) is False:
        return 0
    now = CLOCK.time()
    n = 0
    for u in set(_M3U.values()):
        if len(_IPTV_PEND) >= _IPTV_MAX_PEND:
            break
        if not u.startswith('http') or u in _IPTV_PEND or not _iptv_due(u, now):
            continue
        _IPTV_PEND.add(u)
        try:
            _POOL_BG.submit(_iptv_probe, u)
            n += 1
        except Exception:                                # noqa: BLE001 — pool cerrado
            _IPTV_PEND.discard(u)
    if n:
        log_ev('iptv_sweep', probes=n)
    return n


def _m3u_out(metas):
    """parental: fuera canales con etiquetas adultas; cuarentena IPTV: fuera
    los confirmados muertos — reaparecen solos si el barrido los revive."""
    out = [m for m in metas if not _iptv_dead(_M3U.get(m['id'], ''))]
    if not CFG.get('parental'):
        return out
    from conflux.core.cfg import RE_ADULT           # local: cfg importa lists (circular)
    return [m for m in out
            if not RE_ADULT.search(f"{m.get('name', '')} {m.get('description', '')}")]

def _m3u_extinf(ln):
    """Línea #EXTINF -> {name, logo, group, tvg}."""
    return {'name': ln.rsplit(',', 1)[-1].strip() or 'Canal',
            'logo': (m.group(1) if (m := re.search(r'tvg-logo="([^"]*)"', ln)) else ''),
            'group': (m.group(1) if (m := re.search(r'group-title="([^"]*)"', ln)) else ''),
            'tvg': (m.group(1) if (m := re.search(r'tvg-id="([^"]*)"', ln)) else '')}


_st.health_load(_IPTVH_PATH, _IPTV_BAD, _IPTV_OK, keep=_IPTV_Q_T)  # del proceso anterior


