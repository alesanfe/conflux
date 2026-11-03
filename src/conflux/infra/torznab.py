"""Prowlarr/Jackett Torznab como fuente extra de streams."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import re
import time
import urllib.parse

from conflux.core.state import CFG, _POOL, _health
from conflux.infra.net import _session

try:
    from defusedxml import ElementTree as _ET  # torznab: XML del indexer (no confiable)
except ImportError:
    _ET = None                                     # sin defusedxml: torznab se desactiva (nunca stdlib)


def _torznab(stype, sid):
    """Prowlarr/Jackett (3.5): Torznab directo como fuente extra de streams.
    Config: torznab_url + torznab_key. Prowlarr: http://host:9696/0/api
    (/{id}/api — cualquier id, incluido 0, agrega TODOS los indexers
    habilitados). Jackett: /api/v2.0/indexers/all/results/torznab."""
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


_TZ_IDS = {'ts': 0.0, 'ids': None}   # indexers habilitados en Prowlarr (cache 600s)


def _tz_prowlarr_ids(base, key):
    """Ids de indexers habilitados vía /api/v1/indexer (cache 600s; None = error)."""
    now = time.time()
    if now - _TZ_IDS['ts'] < 600:
        return _TZ_IDS['ids']
    ids = None
    try:
        r = _session(urllib.parse.urlsplit(base).netloc).get(
            f'{base}/api/v1/indexer', params={'apikey': key}, timeout=8)
        if r.ok:
            ids = [i['id'] for i in r.json() if i.get('enable', True)]
    except Exception:                                   # noqa: BLE001 — sin API: se usa el id literal
        pass
    _TZ_IDS.update(ts=now, ids=ids)
    return ids


def _tz_query(url, key, stype, imdb):
    """GET torznab -> streams parseados. Si la URL es estilo Prowlarr
    (/{id}/api), expande a TODOS los indexers habilitados (id 0 = agregado)."""
    m = re.match(r'^(https?://[^/?#]+)/(\d+)/api/?$', url)
    urls = [url]
    if m:
        ids = _tz_prowlarr_ids(m.group(1), key)
        if ids:
            urls = [f'{m.group(1)}/{i}/api' for i in ids]
    params = {'t': 'movie' if stype == 'movie' else 'tvsearch',
              'imdbid': imdb, 'apikey': key, 'extended': '1'}
    tz = '{http://torznab.com/schemas/2015/feed}'

    def _one(u):
        r = _session(urllib.parse.urlsplit(u).netloc).get(u, params=params, timeout=10)
        return [_tz_item(it, attrs={a.get('name'): a.get('value') for a in it.iter(tz + 'attr')})
                for it in _ET.fromstring(r.content).iter('item')]

    out, seen = [], set()
    futs = [_POOL.submit(_one, u) for u in urls]
    for f in futs:
        try:
            for s in f.result(timeout=15):
                if s and s['infoHash'] not in seen:
                    seen.add(s['infoHash'])
                    out.append(s)
        except Exception:                               # noqa: BLE001 — un indexer caído no tumba el resto
            pass
    return out


def _tz_item(it, attrs):
    """<item> torznab -> stream si lleva magnet/btih (o None).
    Prowlarr no expone magnets: el hash viene en torznab:attr infohash."""
    link = it.findtext('link') or ''
    magnet = link if link.startswith('magnet') else attrs.get('magneturl', '')
    mh = re.search(r'btih:([0-9a-fA-F]{40}|[a-zA-Z2-7]{32})', magnet)
    if mh:
        ih = mh.group(1)
    else:
        ih = attrs.get('infohash', '')
        if not re.fullmatch(r'[0-9a-fA-F]{40}|[a-zA-Z2-7]{32}', ih):
            return None
    return {'name': 'Torznab', 'title': it.findtext('title') or 'torrent',
            'infoHash': ih,
            'behaviorHints': {'seeders': _tz_num(attrs.get('seeders'), int),
                              'videoSize': _tz_num(attrs.get('size'), float),
                              'bingeGroup': 'torznab'}}


def _tz_num(v, cast):
    """Attr torznab malformado ('', 'abc', '12,5') -> 0: un item raro no debe
    tumbar todos los resultados del indexer."""
    try:
        return cast(str(v).replace(',', '.').strip() or 0)
    except (TypeError, ValueError):
        return 0
