"""Scrapers integrados — indexers públicos SIN addon upstream (estilo Comet).

YTS (películas, API JSON), EZTV (series, API JSON) y Nyaa (anime/general, RSS)
devuelven torrents con hash real: infoHash + seeds + size -> stream directo,
sin pasar por ningún addon de la red. `scrapers` en config.json limita la
lista; cada uno es un worker propio en _collect (health + contrib separados).

Además `scraper_rss` (lista de URLs de feeds RSS/Atom de indexers) añade
scrapers genéricos: cualquier feed con magnets en <link>/<enclosure>/<guid>
o attrs torznab infohash/magneturl -> streams (un worker `RSS:<host>` por
feed). Cubre indexers con RSS sin scraper dedicado (showrss, feeds propios).

Título para Nyaa: igual que Easynews — del meta que el cliente ya cargó
(`_meta_c`); sin él no hay query (el imdb no le vale a Nyaa).
"""
# pylint: disable=broad-exception-caught
import re
import urllib.parse

from conflux.core.state import CFG, _health, _meta_title
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
    return _meta_title(stype, sid)


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


_RSS_BTIH = re.compile(r'urn:btih:([0-9a-fA-F]{40}|[a-zA-Z2-7]{32})')
_RSS_SIZE_ATTR = ('size', 'contentlength')


def _rss_map():
    """{display_name: feed_url} — scrapers dinámicos de `scraper_rss` (URLs)."""
    want = CFG.get('scraper_rss')
    if not isinstance(want, (list, tuple)):
        return {}
    out = {}
    for i, u in enumerate(want):
        u = str(u).strip()
        if not u.startswith('http'):
            continue
        host = urllib.parse.urlsplit(u).netloc or f'feed{i}'
        out[f'RSS:{host}'] = u                         # host repetido -> último gana
    return out


def _rss_hash(it, attrs, enc):
    """infoHash del item: attr torznab | elemento <ns:infoHash> | magnet/hex40."""
    ih = attrs.get('infohash') or attrs.get('infoHash') or ''
    if not ih:                                       # <ns:infoHash> elemento (nyaa & cia)
        ih = next((el.text.strip() for el in it.iter()
                   if str(el.tag).rsplit('}', maxsplit=1)[-1].lower() == 'infohash'
                   and el.text), '')
    if ih:
        return ih
    vals = [it.findtext('link') or '', it.findtext('guid') or '',
            enc.get('url', '') if enc is not None else '',
            attrs.get('magneturl') or '', attrs.get('magnet') or '']
    for v in vals:
        v = (v or '').strip()
        m = _RSS_BTIH.search(v)
        if m:
            return m.group(1)
        if re.fullmatch(r'[0-9a-fA-F]{40}', v):
            return v
    return ''


def _rss_size(it, attrs, enc):
    """bytes: enclosure@length | attr size/contentlength | nyaa:size texto."""
    if enc is not None and str(enc.get('length') or '').isdigit():
        return int(enc.get('length'))
    for k in _RSS_SIZE_ATTR:
        if str(attrs.get(k) or '').isdigit():
            return int(attrs[k])
    return _nyaa_size(it.findtext(_NYAA + 'size') or it.findtext('size'))


def _rss_item(it):
    """item RSS/Atom -> (title, infohash, size, seeds) o None sin magnet."""
    attrs = {a.get('name'): a.get('value') for a in it.iter()
             if str(a.tag).rsplit('}', maxsplit=1)[-1] == 'attr'}  # torznab/jackett
    enc = it.find('enclosure')
    ih = _rss_hash(it, attrs, enc)
    if not ih:
        return None
    try:
        seeds = int(attrs.get('seeders') or attrs.get('peers') or 0)
    except (TypeError, ValueError):
        seeds = 0
    return it.findtext('title') or 'rss', ih, _rss_size(it, attrs, enc), seeds


def _sc_rss(url, stype, sid):
    """Feed RSS/Atom genérico -> streams con magnet (un worker por feed)."""
    if _ET is None or stype not in ('movie', 'series', 'anime') or not sid:
        return []
    host = urllib.parse.urlsplit(url).netloc
    r = _session(host).get(url, timeout=10)
    out = []
    for it in _ET.fromstring(r.content).iter('item'):
        x = _rss_item(it)
        if x:
            out.append(_stream(f'RSS:{host}', x[0], x[1], x[3], x[2]))
    _health(f'RSS:{host}', True, '', len(out))
    return out[:20]


_SCR_KEY = {'YTS': 'yts', 'EZTV': 'eztv', 'Nyaa': 'nyaa'}
_SCRAPERS = {'yts': ('YTS', _sc_yts), 'eztv': ('EZTV', _sc_eztv),
             'nyaa': ('Nyaa', _sc_nyaa)}


def _scraper_names():
    """Nombres de los scrapers activos (para el fan-out de _collect)."""
    return [_SCRAPERS[k][0] for k in _sc_enabled()] + list(_rss_map())


def _rss_worker(url):
    """Closure de feed RSS concreto -> worker (stype, sid) para _scraper()."""
    def _w(stype, sid):
        return _sc_rss(url, stype, sid)
    return _w


def _scraper(name, stype, sid):
    """Worker por nombre ('YTS'|'EZTV'|'Nyaa'|'RSS:<host>') -> streams o []."""
    fn = next((f for k, (n, f) in _SCRAPERS.items() if n == name), None)
    if fn is None and name in _rss_map():
        fn = _rss_worker(_rss_map()[name])
    if fn is None:
        return []
    try:
        return fn(stype, sid)
    except Exception:                                   # noqa: BLE001 — nunca tumba _collect
        _health(name, False, 'error', 0)
        return []
