"""EPG nativo (Stremio "Native EPG"): XMLTV -> parrilla por canal m3u.

Fuente: `config.epg_url` (epgshare01 ES por defecto — XMLTV público con los
mismos ids de canal que usa iptv-org). Parseo una vez, caché en `epg-cache.json`
(escritura atómica) con TTL `epg_ttl_s` (12h). La primera petición sirve lo
cacheado (o vacío) y refresca en background — nunca bloquea el catálogo.

Formato Stremio: programas como meta.videos con startTime/endTime ISO-UTC.
"""
# pylint: disable=broad-exception-caught
# broad-exception: la guía es best-effort; un XML malo no puede tumbar el catálogo
import gzip
import io
import os
import re
import threading
import time
import unicodedata
import urllib.request
import defusedxml.ElementTree as ET   # XML descargado: defused (igual que torznab)

from conflux.core.state import _POOL_BG, _ROOT, _atomic_json, _log, CFG
from conflux.infra.net import _public_url

_EPG_FILE = os.path.join(_ROOT, 'epg-cache.json')
_EPG_TTL = 12 * 3600


class _ChkRedirect(urllib.request.HTTPRedirectHandler):
    """Un redirect a link-local/metadata sigue siendo SSRF: revalida cada salto."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_url(newurl):
            return None                                  # urllib -> HTTPError, sin seguirlo
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_ChkRedirect())

_LOCK = threading.Lock()
_STATE: dict = {'fetched': 0.0, 'map': {}, 'days': {}}
_LOAD_LOCK = threading.Lock()
_LOADING = [False]


def _norm(s):
    """'La1.es@SD' / 'La 1' / 'La.1.es' -> 'la1' (matching tvg-id<->epg id/nombre)."""
    s = (s or '').split('@')[0].lower()
    s = ''.join(c for c in unicodedata.normalize('NFKD', s)
                if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', s)


def _xmltv_ts(v):
    """'20251103220000 +0100' -> epoch (tolerante a formato)."""
    m = re.match(r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*([+-]\d{4})?', v or '')
    if not m:
        return None
    y, mo, d, h, mi, s, tz = m.groups()
    off = int(tz[:3]) * 3600 + int(tz[3:]) * 60 if tz else 0
    try:
        return time.mktime((int(y), int(mo), int(d), int(h), int(mi), int(s),
                            0, 0, -1)) - time.timezone - off
    except (OverflowError, ValueError):
        return None


def _iso(ts):
    return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(ts))


def _epg_channels(data, m3u_metas):
    """Canales del XMLTV -> (ch_map {m3u_id: epg_id}, want {epg_id: m3u_id})
    mapeando por tvg-id y por nombre (norm de ambos lados)."""
    epgs: dict = {}
    for m in re.finditer(r'<channel id="([^"]+)"[^>]*>\s*<display-name[^>]*>([^<]+)<',
                         data.decode('utf-8', 'replace')):
        epgs.setdefault(_norm(m.group(1)), m.group(1))
        epgs.setdefault(_norm(m.group(2)), m.group(1))
    ch_map: dict = {}
    want: dict = {}                                   # epg_id -> m3u cid
    for m in m3u_metas:
        eid = epgs.get(_norm(m.get('tvg', ''))) or epgs.get(_norm(m.get('name', '')))
        if eid:
            ch_map[m['id']] = eid
            want[eid] = m['id']
    return ch_map, want


def _epg_programmes(root, want):
    """<programme> del XMLTV -> {dia: {cid: [progs]}} — solo canales mapeados
    en la ventana ayer -> +2d, ordenados por start."""
    now = time.time()
    lo, hi = now - 26 * 3600, now + 2 * 86400         # ayer -> +2d
    days: dict = {}
    for p in root.iter('programme'):
        cid = want.get(p.get('channel'))
        if cid is None:
            continue
        ts = _xmltv_ts(p.get('start'))
        te = _xmltv_ts(p.get('stop'))
        if ts is None or te is None or not lo <= ts <= hi:
            continue
        prog = {'title': (p.findtext('title') or '').strip(),
                'desc': (p.findtext('desc') or '').strip(),
                'icon': (ic.get('src') if (ic := p.find('icon')) is not None else ''),
                'start': ts, 'stop': te}
        days.setdefault(time.strftime('%Y-%m-%d', time.gmtime(ts)), {}) \
            .setdefault(cid, []).append(prog)
    for chs in days.values():
        for progs in chs.values():
            progs.sort(key=lambda x: x['start'])
    return days


def _build(m3u_metas):
    """Descarga + parsea el XMLTV -> {'map': {cid: epg_id}, 'days': {dia: {cid: [p]}}}.
    Solo conserva programas de canales mapeados (ventana: ayer -> +2d)."""
    url = CFG.get('epg_url') or ''
    if not url or not _public_url(url):                  # SSRF: http(s) + host no link-local
        return None
    req = urllib.request.Request(url, headers={'User-Agent': 'conflux-epg/1.0'})
    raw = _OPENER.open(req, timeout=60).read(40_000_000)
    if not raw:
        return None
    try:
        data = gzip.decompress(raw)
    except OSError:
        data = raw                                   # por si el XML no va gz
    ch_map, want = _epg_channels(data, m3u_metas)
    if not want:
        return None
    days = _epg_programmes(ET.parse(io.BytesIO(data)).getroot(), want)
    _log.info('epg: %d canales mapeados, %d dias, %d programas', len(ch_map),
              len(days), sum(len(p) for d in days.values() for p in d.values()))
    return {'map': ch_map, 'days': days}


def _reload():
    try:
        import json
        from conflux.domain.lists import _m3u_metas
        if os.path.exists(_EPG_FILE) and _STATE['fetched'] == 0:
            try:                                     # caché en disco -> caliente
                with open(_EPG_FILE, encoding='utf-8') as _efh:
                    d = json.load(_efh)
                if d.get('days'):
                    _STATE.update(d)
                    _log.info('epg: cache disco (%d canales)', len(d.get('map', {})))
                    if time.time() - d.get('fetched', 0) < _EPG_TTL:
                        return
            except (OSError, ValueError):
                pass
        metas = _m3u_metas()
        if not metas:
            return
        built = _build(metas)
        if built:
            _STATE['map'], _STATE['days'] = built['map'], built['days']
            _STATE['fetched'] = time.time()
            _atomic_json(_EPG_FILE, _STATE)
    except Exception as e:                           # noqa: BLE001 — bg: nunca romper
        _log.warning('epg reload: %r', e)
    finally:
        _LOADING[0] = False


def epg_kick():
    """Dispara una recarga en background si el EPG falta o está viejo."""
    stale = (not _STATE['days']) or time.time() - _STATE['fetched'] > _EPG_TTL
    if not stale:
        return
    with _LOAD_LOCK:
        if _LOADING[0]:
            return                                   # ya hay una recarga en vuelo
        _LOADING[0] = True
    _POOL_BG.submit(_reload)


def _videos(cid, day):
    """Programas de `cid` el día `day` -> meta.videos del protocolo EPG."""
    progs = _STATE['days'].get(day, {}).get(cid) or []
    out = []
    for i, p in enumerate(progs):
        out.append({'id': f'{cid}:{i}', 'name': p['title'] or 'Programa',
                    'season': 1, 'episode': i + 1, 'number': i + 1,
                    'released': _iso(p['start']), 'firstAired': _iso(p['start']),
                    'startTime': _iso(p['start']), 'endTime': _iso(p['stop']),
                    'thumbnail': p['icon'], 'overview': p['desc']})
    return out


def epg_metas(day, metas):
    """Catálogo con date=día -> metasDetailed: canales + sus programas ese día."""
    epg_kick()
    out = []
    for m in metas:
        m = dict(m)
        m['videos'] = _videos(m['id'], day)
        out.append(m)
    return {'metasDetailed': out}


def epg_meta(m):
    """Meta de un canal -> videos de hoy (y cola de mañana) — spec: 'nearby'."""
    epg_kick()
    if not m:
        return {}
    m = dict(m)
    today = time.strftime('%Y-%m-%d', time.gmtime())
    nxt = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 86400))
    vids = _videos(m['id'], today)
    vids += _videos(m['id'], nxt)[:6]                 # un poco de mañana
    for v in vids[len(_videos(m['id'], today)):]:
        v['id'] = f"{m['id']}:n{v['id'].rsplit(':', 1)[-1]}"
    m['videos'] = vids
    return m
