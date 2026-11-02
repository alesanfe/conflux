"""Catálogos externos (Trakt/IMDb/MDBList) y M3U local."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import re
from contextlib import suppress

import requests

from .feeds import _trakt_h
from .net import _session, fetch
from .state import _POOL_BG, CFG, SOURCES, _cat_c, _cget, _cset, _open

_LLIST = {}

def _list_defs():
    """config.catalog_lists: URLs (Trakt/IMDb/MDBList) separadas por coma."""
    return [x.strip() for x in (CFG.get('catalog_lists') or '').split(',') if x.strip()]


def _list_trakt(url):
    m = re.search(r'trakt\.tv/users/([^/]+)/lists/([^/?]+)', url)
    if not m:
        raise ValueError('url trakt no reconocida')
    s = _session('api.trakt.tv')
    meta = s.get(f'https://api.trakt.tv/users/{m[1]}/lists/{m[2]}',
                 headers=_trakt_h(), timeout=8).json()
    rows = s.get(f'https://api.trakt.tv/users/{m[1]}/lists/{m[2]}/items?extended=full&limit=200',
                 headers=_trakt_h(), timeout=10).json()
    items = [('movie' if e.get('movie') else 'series', 'tt' + str(ids['imdb']).lstrip('t'))
             for e in rows if (ids := (e.get('movie') or e.get('show') or {}).get('ids') or {}).get('imdb')]
    return meta.get('name') or 'Lista Trakt', items


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
    items = [('series' if it.get('mediatype') == 'show' else 'movie',
              'tt' + str(it['imdb_id']).lstrip('t'))
             for it in (d.get('items') or []) if it.get('imdb_id')]
    return d.get('name') or f'MDBList {lid}', items


def _list_fetch(url):
    """Lista externa -> (nombre, [(type, tt_id)])  — cacheada en _cat_c."""
    hit = _cget(_cat_c, ('list', url))
    if hit is not None:
        return hit
    name, items = 'Lista', []
    with suppress(requests.RequestException, TypeError, ValueError, KeyError, AttributeError):
        if 'trakt.tv' in url:
            name, items = _list_trakt(url)
        elif 'imdb.com' in url:
            name, items = _list_imdb(url)
        else:
            name, items = _list_mdblist(url)
    res = (name, items)
    _cset(_cat_c, ('list', url), res)
    return res


def _list_metas(url, utype):
    """Items de lista -> metas (resueltos via Cinemeta por tt)."""
    _, items = _list_fetch(url)
    ids = [i for t, i in items if t == utype]
    cine = next(b for n, b in SOURCES if n == 'Cinemeta')
    out = []
    for f in _POOL_BG.map(lambda i: fetch(f"{cine}/meta/{utype}/{i}.json", 8), ids):
        m = (f or {}).get('meta')
        if m:
            out.append(m)
    return {'metas': out}



_M3U = {}

def _m3u_metas():
    """Parsea config.m3u_path -> metas tv + streams indexados (m3u:N)."""
    try:
        txt = _open(CFG['m3u_path'], encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    metas = []
    _M3U.clear()
    name = logo = group = ''
    n = 0
    for raw in txt.splitlines():
        ln = raw.strip()
        if ln.startswith('#EXTINF'):
            name = ln.rsplit(',', 1)[-1].strip() or 'Canal'
            lm = re.search(r'tvg-logo="([^"]*)"', ln)
            logo = lm.group(1) if lm else ''
            gm = re.search(r'group-title="([^"]*)"', ln)
            group = gm.group(1) if gm else ''
        elif ln and not ln.startswith('#'):
            n += 1
            cid = f'm3u:{n}'
            _M3U[cid] = ln
            metas.append({'id': cid, 'type': 'tv', 'name': name,
                          'poster': logo or 'https://i.imgur.com/tTCuczU.png',
                          'posterShape': 'square',
                          'description': group})
            name = logo = group = ''
    return metas
