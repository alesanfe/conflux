"""Prowlarr/Jackett Torznab como fuente extra de streams."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import re
import urllib.parse
import urllib.request

from .net import _session
from .state import CFG, _health

try:
    from defusedxml import ElementTree as _ET  # torznab: XML del indexer (no confiable)
except ImportError:
    _ET = None                                     # sin defusedxml: torznab se desactiva (nunca stdlib)


def _torznab(stype, sid):
    """Prowlarr/Jackett (3.5): Torznab directo como fuente extra de streams.
    Config: torznab_url (p.ej. http://localhost:9696/api/v1.0/indexers/all/results/torznab)
    + torznab_key."""
    url, key = CFG.get('torznab_url', '').rstrip('/'), CFG.get('torznab_key', '')
    imdb = sid.split(':')[0]
    if not url or not key or not imdb.startswith('tt') or _ET is None:
        return []
    try:
        out = _tz_query(url, key, stype, imdb)
        _health('Torznab', True, '', len(out))
        return out
    except Exception:                                   # noqa: BLE001 — indexer local: nunca tumbar _collect
        _health('Torznab', False, 'error', 0)
        return []


def _tz_query(url, key, stype, imdb):
    """GET torznab -> streams parseados."""
    r = _session(urllib.parse.urlsplit(url).netloc).get(url,
        params={'t': 'movie' if stype == 'movie' else 'tvsearch',
                'imdbid': imdb, 'apikey': key, 'extended': '1'},
        timeout=10)
    tz = '{http://torznab.com/schemas/2015/feed}'
    return [s for it in _ET.fromstring(r.content).iter('item')
            if (s := _tz_item(it, attrs={a.get('name'): a.get('value') for a in it.iter(tz + 'attr')}))]


def _tz_item(it, attrs):
    """<item> torznab -> stream si lleva magnet con btih (o None)."""
    link = it.findtext('link') or ''
    magnet = link if link.startswith('magnet') else attrs.get('magneturl', '')
    mh = re.search(r'btih:([0-9a-fA-F]{40}|[a-zA-Z2-7]{32})', magnet)
    if not mh:
        return None
    return {'name': 'Torznab', 'title': it.findtext('title') or 'torrent',
            'infoHash': mh.group(1),
            'behaviorHints': {'seeders': int(attrs.get('seeders') or 0),
                              'videoSize': float(attrs.get('size') or 0),
                              'bingeGroup': 'torznab'}}
