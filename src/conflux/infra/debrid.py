"""Debrid: detección de caché, marcado instant y resolve de magnets."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import os
from contextlib import suppress

import requests

from conflux.core.state import CFG, PORT
from conflux.infra.net import _session

# ---- debrid (Real-Debrid / TorBox) — activo solo con key en config.json/env ----
_DB_OK: dict = {}

def _db_cfg():
    p, k = CFG.get('debrid_provider'), CFG.get('debrid_key') or os.environ.get('DEBRID_KEY', '')
    return (p, k) if p and k else (None, None)


def _dbc_rd(k, miss):
    r = _session('api.real-debrid.com').get(
        'https://api.real-debrid.com/rest/1.0/torrents/instantAvailability/' + '/'.join(miss[:40]),
        headers={'Authorization': f'Bearer {k}'}, timeout=6)
    d = r.json()
    for h in miss[:40]:
        e = d.get(h) or {}
        _DB_OK[h] = bool(e.get('rd') or e.get('files'))


def _dbc_tb(k, miss):
    r = _session('api.torbox.app').get(
        'https://api.torbox.app/v1/api/torrents/checkcached?hash=' + ','.join(miss[:40]),
        headers={'Authorization': f'Bearer {k}'}, timeout=6)
    ok = set((r.json().get('data') or {}).keys())
    for h in miss[:40]:
        _DB_OK[h] = h.lower() in ok


def _dbc_ad(k, miss):
    r = _session('api.alldebrid.com').post(
        'https://api.alldebrid.com/v4/magnet/instant',
        params={'agent': 'conflux', 'apikey': k},
        data=[('magnets[]', f'magnet:?xt=urn:btih:{h}') for h in miss[:40]], timeout=6)
    mags = {m.get('magnet', '').rsplit(':', maxsplit=1)[-1].lower(): m.get('instant')
            for m in ((r.json().get('data') or {}).get('magnets') or [])}
    for h in miss[:40]:
        _DB_OK[h] = bool(mags.get(h.lower()))


def _dbc_pm(k, miss):
    r = _session('www.premiumize.me').post(
        f'https://www.premiumize.me/api/cache/check?apikey={k}',
        data=[('items[]', f'magnet:?xt=urn:btih:{h}') for h in miss[:40]], timeout=6)
    flags = r.json().get('response') or []
    for h, ok in zip(miss[:40], flags, strict=False):
        _DB_OK[h] = bool(ok)


def _dbc_ed(k, miss):
    r = _session('easydebrid.com').post(
        'https://easydebrid.com/api/v1/link/lookup',
        json={'urls': [f'magnet:?xt=urn:btih:{h}' for h in miss[:40]]},
        headers={'Authorization': f'Bearer {k}'}, timeout=6)
    res = r.json()
    lst = res if isinstance(res, list) else (res.get('data') or res.get('results') or [])
    hit = {str(x.get('url','')).rsplit(':', maxsplit=1)[-1].lower(): x.get('cached', True)
           for x in lst if isinstance(x, dict)}
    for h in miss[:40]:
        _DB_OK[h] = bool(hit.get(h.lower(), h.lower() in hit))


def _dbc_dl(k, miss):
    ok_ = set()
    for h in miss[:20]:                            # API: una petición por magnet
        r = _session('debrid-link.com').get(
            'https://debrid-link.com/api/v2/seedbox/cached',
            params={'url': f'magnet:?xt=urn:btih:{h}'},
            headers={'Authorization': f'Bearer {k}'}, timeout=6)
        if r.json().get('value'):
            ok_.add(h)
    for h in miss[:20]:
        _DB_OK[h] = h in ok_


_DB_CHECK = {'real-debrid': _dbc_rd, 'torbox': _dbc_tb, 'alldebrid': _dbc_ad,
             'premiumize': _dbc_pm, 'easydebrid': _dbc_ed, 'debrid-link': _dbc_dl}


def _db_avail(hashes):
    """Hashes cacheados en el debrid -> set (lote, cacheado en _DB_OK)."""
    p, k = _db_cfg()
    miss = [h for h in hashes if h not in _DB_OK]
    fn = _DB_CHECK.get(p) if p else None
    if fn and miss:
        with suppress(requests.RequestException, TypeError, ValueError, KeyError):
            fn(k, miss)
    return {h for h in hashes if _DB_OK.get(h)}


def _debrid_mark(streams):
    p, _ = _db_cfg()
    if not p:
        return
    hashes = [s.get('infoHash') for s in streams if s.get('infoHash')]
    if not hashes:
        return
    ok = _db_avail(hashes)
    for i, s in enumerate(streams):
        if s.get('infoHash') in ok:
            streams[i] = dict(s)                # copia: los dicts viven en _cache —
            _db_mark_one(p, streams[i])         # mutarlos envenena el caché con /r/


def _db_mark_one(p, s):
    """Stream cacheado -> rayo + url /r/ (infoHash y sources ya no hacen falta)."""
    h = s['infoHash']
    s['cached'] = True
    s['name'] = '⚡ ' + (s.get('name') or '')
    s['url'] = f'http://127.0.0.1:{PORT}/r/{p}/{h}?fi={s.get("fileIdx", 0)}'
    s.pop('infoHash', None)
    s.pop('sources', None)


_VIDEO_EXT = ('.mp4', '.mkv', '.avi', '.m4v', '.webm')


def _pick_file(fs, fi, default=None):
    """file_idx del stream -> fichero del torrent; fuera de rango = default."""
    return fs[int(fi)] if int(fi) < len(fs) else default


def _db_rd(k, h, fi):
    """Real-Debrid: addMagnet -> selectFiles -> unrestrict/link."""
    h_ = {'Authorization': f'Bearer {k}'}
    s = _session('api.real-debrid.com')
    t = s.post('https://api.real-debrid.com/rest/1.0/torrents/addMagnet',
               data={'magnet': f'magnet:?xt=urn:btih:{h}'}, headers=h_, timeout=8).json()
    tid = t['id']
    inf = s.get(f'https://api.real-debrid.com/rest/1.0/torrents/info/{tid}', headers=h_, timeout=8).json()
    fs = [f for f in inf.get('files', []) if f.get('name', '').lower().endswith(_VIDEO_EXT)]
    fsel = fs[int(fi)] if int(fi) < len(fs) else max(fs, key=lambda f: f.get('bytes', 0))
    s.post(f'https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{tid}',
           data={'files': fsel['id']}, headers=h_, timeout=8)
    inf = s.get(f'https://api.real-debrid.com/rest/1.0/torrents/info/{tid}', headers=h_, timeout=8).json()
    link = (inf.get('links') or [None])[fs.index(fsel) if fsel in fs else 0]
    d = s.post('https://api.real-debrid.com/rest/1.0/unrestrict/link',
               data={'link': link}, headers=h_, timeout=8).json()
    return d.get('download')


def _db_tb(k, h, fi):
    s = _session('api.torbox.app')
    h_ = {'Authorization': f'Bearer {k}'}
    t = s.post('https://api.torbox.app/v1/api/torrents/createtorrent',
               data={'magnet': f'magnet:?xt=urn:btih:{h}'}, headers=h_, timeout=10).json()
    tid = (t.get('data') or {}).get('torrent_id')
    lnk = s.get('https://api.torbox.app/v1/api/torrents/requestdl',
                params={'token': k, 'torrent_id': tid, 'file_id': fi},
                headers=h_, timeout=10).json()
    return (lnk.get('data') or {}).get('link') or (lnk.get('data'))


def _db_ad(k, h, fi):
    s = _session('api.alldebrid.com')
    q = {'agent': 'conflux', 'apikey': k}
    up = s.post('https://api.alldebrid.com/v4/magnet/upload',
                params=q, data={'magnets[]': f'magnet:?xt=urn:btih:{h}'}, timeout=10).json()
    mid = ((up.get('data') or {}).get('magnets') or [{}])[0].get('id')
    st = s.get('https://api.alldebrid.com/v4/magnet/status',
               params={**q, 'id': mid}, timeout=10).json()
    m = (st.get('data') or {}).get('magnets') or {}
    fsel = _pick_file(_video_links(m.get('links', [])), fi)
    if not fsel:
        return None
    d = s.get('https://api.alldebrid.com/v4/link/unlock',
              params={**q, 'link': fsel['link']}, timeout=10).json()
    return ((d.get('data') or {}).get('link'))

def _video_links(links, name_key='filename'):
    """links/files debrid -> solo los de vídeo (o entries no-dict fuera)."""
    return [f for f in links if isinstance(f, dict)
            and str(f.get(name_key) or f.get('name') or '').lower().endswith(_VIDEO_EXT)]


def _db_pm(k, h, fi):
    s = _session('www.premiumize.me')
    d = s.post(f'https://www.premiumize.me/api/transfer/directdl?apikey={k}',
               data={'src': f'magnet:?xt=urn:btih:{h}'}, timeout=15).json()
    fs = [f for f in (d.get('content') or []) if str(f.get('path', '')).lower().endswith(_VIDEO_EXT)]
    if not fs:
        return None
    fsel = fs[int(fi)] if int(fi) < len(fs) else max(fs, key=lambda f: f.get('size', 0))
    return fsel.get('link')


def _db_ed(k, h, fi):
    s = _session('easydebrid.com')
    h_ = {'Authorization': f'Bearer {k}'}
    d = s.post('https://easydebrid.com/api/v1/link/generate',
               json={'url': f'magnet:?xt=urn:btih:{h}'}, headers=h_, timeout=10).json()
    files = _video_links(d.get('files') or d.get('data') or [])
    if files:
        fsel = files[int(fi)] if int(fi) < len(files) else files[0]
        return fsel.get('url') or fsel.get('link')
    return d.get('url') or d.get('link')


def _db_dl(k, h, fi):
    s = _session('debrid-link.com')
    h_ = {'Authorization': f'Bearer {k}'}
    add = s.post('https://debrid-link.com/api/v2/seedbox/add',
                 json={'url': f'magnet:?xt=urn:btih:{h}', 'async': True},
                 headers=h_, timeout=10).json()
    tid = (add.get('value') or {}).get('id')
    fs = s.get(f'https://debrid-link.com/api/v2/seedbox/{tid}/files',
               headers=h_, timeout=10).json().get('value') or []
    fs = [f for f in fs if str(f.get('name', '')).lower().endswith(_VIDEO_EXT)]
    fsel = _pick_file(fs, fi, fs[0] if fs else None)
    return fsel.get('downloadUrl') if fsel else None


_DB_RESOLVE = {'real-debrid': _db_rd, 'torbox': _db_tb, 'alldebrid': _db_ad,
               'premiumize': _db_pm, 'easydebrid': _db_ed, 'debrid-link': _db_dl}


def _db_resolve(p, h, fi):
    """Resuelve infoHash -> URL HTTP final del debrid (redirect 302)."""
    _, k = _db_cfg()
    fn = _DB_RESOLVE.get(p)
    if not k or not fn:
        return None
    try:
        fi = int(fi)                       # ?fi= del query: malformado -> 0, no crash
    except (TypeError, ValueError):
        fi = 0
    try:
        return fn(k, h, fi)
    except Exception:                                            # noqa: BLE001 — boundary: debrid devuelve None, nunca crash
        return None
