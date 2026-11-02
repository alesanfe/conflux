"""Catálogos externos (Trakt/IMDb/MDBList) y M3U local."""
# pylint: disable=broad-exception-caught,line-too-long

# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import re
from contextlib import suppress

import requests

from conflux.core.state import (
    _POOL_BG,
    _YEARS,
    CFG,
    SOURCES,
    _cat_c,
    _cget,
    _cset,
    _open,
    as_dict,
)
from conflux.infra.net import _session, fetch
from conflux.infra.trakt import _trakt_h

_LLIST: dict = {}

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
    return meta.get('name') or 'Lista Trakt', _trakt_items(rows)


def _trakt_items(rows):
    """items trakt -> [(type, tt_id)]."""
    return [('movie' if as_dict(e).get('movie') else 'series', 'tt' + str(ids['imdb']).lstrip('t'))
            for e in rows if isinstance(e, dict)
            and (ids := as_dict(as_dict(e).get('movie') or as_dict(e).get('show')).get('ids')).get('imdb')]


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
        m = as_dict(f).get('meta')
        if m:
            out.append(m)
    return {'metas': out}



_M3U: dict = {}

_M3U_METAS: list = []
_M3U_TXT = [None]        # contenido ya parseado — no re-parsear por request


def _m3u_metas():
    """Parsea config.m3u_path -> metas tv + streams indexados (m3u:N).
    Lee el fichero en cada llamada pero solo re-parsea si el contenido cambió."""
    try:
        txt = _open(CFG['m3u_path'], encoding='utf-8', errors='replace').read()
    except OSError:
        return []
    if txt == _M3U_TXT[0]:
        return [{**m} for m in _M3U_METAS]
    metas = []
    _M3U.clear()
    cur = {'name': '', 'logo': '', 'group': ''}
    for raw in txt.splitlines():
        ln = raw.strip()
        if ln.startswith('#EXTINF'):
            cur = _m3u_extinf(ln)
        elif ln and not ln.startswith('#'):
            cid = f'm3u:{len(metas) + 1}'
            _M3U[cid] = ln
            metas.append({'id': cid, 'type': 'tv', 'name': cur['name'],
                          'poster': cur['logo'] or 'https://i.imgur.com/tTCuczU.png',
                          'posterShape': 'square',
                          'description': cur['group']})
            cur = {'name': '', 'logo': '', 'group': ''}
    _M3U_TXT[0] = txt
    _M3U_METAS[:] = metas
    return metas

def _m3u_extinf(ln):
    """Línea #EXTINF -> {name, logo, group}."""
    return {'name': ln.rsplit(',', 1)[-1].strip() or 'Canal',
            'logo': (m.group(1) if (m := re.search(r'tvg-logo="([^"]*)"', ln)) else ''),
            'group': (m.group(1) if (m := re.search(r'group-title="([^"]*)"', ln)) else '')}



def tmdb_year():
    """Año actual (etiqueta/picker de los catálogos de estrenos)."""
    tmdb = next(b for n, b in SOURCES if n == 'TMDB')
    return f"{tmdb}/catalog/movie/tmdb.year/genre={_YEARS[0]}.json"
