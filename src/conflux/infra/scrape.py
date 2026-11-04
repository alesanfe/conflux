"""Scrapers integrados — indexers públicos SIN addon upstream (estilo Comet).

YTS (películas, API JSON), EZTV (series, API JSON) y Nyaa (anime/general, RSS)
devuelven torrents con hash real: infoHash + seeds + size -> stream directo,
sin pasar por ningún addon de la red. `scrapers` en config.json limita la
lista; cada uno es un worker propio en _collect (health + contrib separados).

Título para Nyaa: igual que Easynews — del meta que el cliente ya cargó
(`_meta_c`); sin él no hay query (el imdb no le vale a Nyaa).
"""
# pylint: disable=broad-exception-caught
import re

from conflux.core.state import CFG, _cget, _health, _meta_c
from conflux.infra.net import _session

try:
    from defusedxml import ElementTree as _ET        # RSS del indexer (no confiable)
except ImportError:
    _ET = None

_NYAA = '{https://nyaa.si/xmlns/nyaa}'
_TRACKERS = ['tracker:udp://tracker.opentrackr.org:1337/announce',
             'tracker:udp://open.stealth.si:80/announce',
             'tracker:udp://tracker.openbittorrent.com:6969/announce']


def _sc_enabled():
    """Lista de scrapers activos (config.scrapers; clave desconocida se ignora)."""
    want = CFG.get('scrapers')
    if not isinstance(want, (list, tuple)):
        return list(_SCRAPERS)
    return [x for x in want if x in _SCRAPERS]


def _sc_title(stype, sid):
    """(imdb, título, sXXeYY) — título del meta cacheado; '' si aún no está."""
    imdb = sid.split(':')[0]
    meta = (_cget(_meta_c, (stype, imdb)) or {}).get('meta') or {}
    t = (meta.get('name') or meta.get('title') or '').strip()
    parts = sid.split(':')
    se = None
    if len(parts) >= 3 and all(x.isdigit() for x in parts[1:3]):
        se = f's{int(parts[1]):02d}e{int(parts[2]):02d}'
    return imdb, t, se


def _stream(name, title, ih, seeds=0, size=0):
    """Torrent de indexer -> stream Stremio (hash + trackers + hints reales)."""
    s: dict = {'name': name, 'title': title,
               'infoHash': ih.lower(),
               'sources': list(_TRACKERS),
               'behaviorHints': {'bingeGroup': f'sc-{_SCR_KEY.get(name, name)}-{ih.lower()}'}}
    if seeds:
        s['behaviorHints']['seeders'] = int(seeds)
    if size:
        s['behaviorHints']['videoSize'] = int(size)
    return s


def _sc_yts(stype, sid):
    """YTS movie_details por imdb -> torrents (solo movies con id tt)."""
    imdb, _, _ = _sc_title(stype, sid)
    if stype != 'movie' or not re.fullmatch(r'tt\d{5,10}', imdb):
        return []
    d = _session('yts.lt').get('https://yts.lt/api/v2/movie_details.json',
                               params={'imdb_id': imdb}, timeout=10).json()
    mv = ((d.get('data') or {}).get('movie')) or {}
    out = []
    for t in mv.get('torrents') or []:
        ih = t.get('hash')
        if not ih:
            continue
        title = f"{mv.get('title_long') or mv.get('title') or 'yts'} " \
                f"[{t.get('quality','?')} {t.get('type','')}] 👤 {t.get('seeds',0)}"
        out.append(_stream('YTS', title, ih, t.get('seeds'), t.get('size_bytes')))
    _health('YTS', True, '', len(out))
    return out[:20]


def _sc_eztv(stype, sid):
    """EZTV get-torrents por imdb -> episodios; filtra S/E del sid."""
    imdb, _, _ = _sc_title(stype, sid)
    parts = sid.split(':')
    if stype != 'series' or not re.fullmatch(r'tt\d{5,10}', imdb):
        return []
    d = _session('eztvx.to').get('https://eztvx.to/api/get-torrents',
                                 params={'imdb_id': imdb[2:]}, timeout=10).json()
    wse, wep = (int(parts[1]), int(parts[2])) if len(parts) >= 3 \
        and all(x.isdigit() for x in parts[1:3]) else (None, None)
    out = []
    for t in (d.get('torrents') or []):
        ih = t.get('hash')
        if not ih:
            continue
        try:
            se, ep = int(t.get('season', 0)), int(t.get('episode', 0))
        except (TypeError, ValueError):
            continue
        if wse is not None and (se, ep) != (wse, wep):
            continue
        title = f"{t.get('filename') or 'eztv'} 👤 {t.get('seeds',0)}"
        out.append(_stream('EZTV', title, ih, t.get('seeds'), t.get('size_bytes')))
    _health('EZTV', True, '', len(out))
    return out[:20]


def _sc_nyaa(stype, sid):
    """Nyaa RSS por título (del meta cacheado) -> anime. c=1_2: anime en inglés."""
    if _ET is None or stype not in ('series', 'anime', 'movie'):
        return []
    imdb, title, se = _sc_title(stype, sid)
    if not imdb.startswith('tt') or not title:
        return []                                       # sin meta -> sin query
    q = f'{title} {se}' if se else title
    r = _session('nyaa.si').get('https://nyaa.si/',
                                params={'page': 'rss', 'q': q, 'c': '1_2', 'f': '0'},
                                timeout=10)
    out = []
    for it in _ET.fromstring(r.content).iter('item'):
        ih = it.findtext(_NYAA + 'infoHash')
        if not ih:
            continue
        size = _nyaa_size(it.findtext(_NYAA + 'size'))
        try:
            seeds = int(it.findtext(_NYAA + 'seeders') or 0)
        except (TypeError, ValueError):
            seeds = 0
        out.append(_stream('Nyaa', it.findtext('title') or 'nyaa',
                           ih, seeds, size))
    _health('Nyaa', True, '', len(out))
    return out[:20]


def _nyaa_size(txt):
    """'1.4 GiB' / '650 MiB' del RSS -> bytes."""
    try:
        num, _, unit = (txt or '0').partition(' ')
        return int(float(num) * {'KiB': 1 << 10, 'MiB': 1 << 20,
                                 'GiB': 1 << 30, 'TiB': 1 << 40}.get(unit, 1))
    except (TypeError, ValueError):
        return 0


_SCR_KEY = {'YTS': 'yts', 'EZTV': 'eztv', 'Nyaa': 'nyaa'}
_SCRAPERS = {'yts': ('YTS', _sc_yts), 'eztv': ('EZTV', _sc_eztv),
             'nyaa': ('Nyaa', _sc_nyaa)}


def _scraper_names():
    """Nombres de los scrapers activos (para el fan-out de _collect)."""
    return [_SCRAPERS[k][0] for k in _sc_enabled()]


def _scraper(name, stype, sid):
    """Worker de scraper por nombre ('YTS'|'EZTV'|'Nyaa') -> streams o []."""
    fn = next((f for k, (n, f) in _SCRAPERS.items() if n == name), None)
    if fn is None:
        return []
    try:
        return fn(stype, sid)
    except Exception:                                   # noqa: BLE001 — nunca tumba _collect
        _health(name, False, 'error', 0)
        return []
