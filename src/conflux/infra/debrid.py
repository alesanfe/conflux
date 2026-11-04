"""Debrid: detección de caché, marcado instant y resolve de magnets."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import requests

from conflux.core.state import CFG, PORT
from conflux.infra.net import _session

# ---- debrid (Real-Debrid / TorBox) — activo solo con key en config.json/env ----
_DB_OK: dict = {}

_DB_SHORT = {'real-debrid': 'RD', 'torbox': 'TB', 'alldebrid': 'AD',
             'premiumize': 'PM', 'easydebrid': 'ED', 'debrid-link': 'DL',
             'offcloud': 'OC', 'putio': 'PIO'}


def _db_cfgs():
    """[(provider, key)] configurados, en orden de prioridad.

    Multi-debrid: `debrid_providers` (dict provider->key) añade providers;
    `debrid_provider`+`debrid_key` (legacy singular) van primero si existen.
    """
    out, seen = [], set()
    p = CFG.get('debrid_provider')
    k = CFG.get('debrid_key') or os.environ.get('DEBRID_KEY', '')
    if p and k:
        out.append((p, k))
        seen.add(p)
    extra = CFG.get('debrid_providers') or {}
    if isinstance(extra, dict):
        for pp, kk in extra.items():
            if pp in _DB_CHECK and kk and pp not in seen:
                out.append((pp, str(kk)))
                seen.add(pp)
    return out


def _db_cfg():
    cfgs = _db_cfgs()
    return cfgs[0] if cfgs else (None, None)


def _db_key(p):
    """API key del provider p entre los configurados (multi-debrid)."""
    return next((k for pp, k in _db_cfgs() if pp == p), None)


def _dbc_rd(k, miss, out=None):
    out = _DB_OK if out is None else out
    r = _session('api.real-debrid.com').get(
        'https://api.real-debrid.com/rest/1.0/torrents/instantAvailability/' + '/'.join(miss[:40]),
        headers={'Authorization': f'Bearer {k}'}, timeout=6)
    d = r.json()
    for h in miss[:40]:
        e = d.get(h) or {}
        out[h] = bool(e.get('rd') or e.get('files'))


def _dbc_tb(k, miss, out=None):
    out = _DB_OK if out is None else out
    r = _session('api.torbox.app').get(
        'https://api.torbox.app/v1/api/torrents/checkcached?hash=' + ','.join(miss[:40]),
        headers={'Authorization': f'Bearer {k}'}, timeout=6)
    ok = set((r.json().get('data') or {}).keys())
    for h in miss[:40]:
        out[h] = h.lower() in ok


def _dbc_ad(k, miss, out=None):
    out = _DB_OK if out is None else out
    r = _session('api.alldebrid.com').post(
        'https://api.alldebrid.com/v4/magnet/instant',
        params={'agent': 'conflux', 'apikey': k},
        data=[('magnets[]', f'magnet:?xt=urn:btih:{h}') for h in miss[:40]], timeout=6)
    mags = {m.get('magnet', '').rsplit(':', maxsplit=1)[-1].lower(): m.get('instant')
            for m in ((r.json().get('data') or {}).get('magnets') or [])}
    for h in miss[:40]:
        out[h] = bool(mags.get(h.lower()))


def _dbc_pm(k, miss, out=None):
    out = _DB_OK if out is None else out
    r = _session('www.premiumize.me').post(
        f'https://www.premiumize.me/api/cache/check?apikey={k}',
        data=[('items[]', f'magnet:?xt=urn:btih:{h}') for h in miss[:40]], timeout=6)
    flags = r.json().get('response') or []
    for h, ok in zip(miss[:40], flags, strict=False):
        out[h] = bool(ok)


def _dbc_ed(k, miss, out=None):
    out = _DB_OK if out is None else out
    r = _session('easydebrid.com').post(
        'https://easydebrid.com/api/v1/link/lookup',
        json={'urls': [f'magnet:?xt=urn:btih:{h}' for h in miss[:40]]},
        headers={'Authorization': f'Bearer {k}'}, timeout=6)
    res = r.json()
    lst = res if isinstance(res, list) else (res.get('data') or res.get('results') or [])
    hit = {str(x.get('url','')).rsplit(':', maxsplit=1)[-1].lower(): x.get('cached', True)
           for x in lst if isinstance(x, dict)}
    for h in miss[:40]:
        out[h] = bool(hit.get(h.lower(), h.lower() in hit))


def _dbc_dl(k, miss, out=None):
    out = _DB_OK if out is None else out
    ok_ = set()
    for h in miss[:20]:                            # API: una petición por magnet
        r = _session('debrid-link.com').get(
            'https://debrid-link.com/api/v2/seedbox/cached',
            params={'url': f'magnet:?xt=urn:btih:{h}'},
            headers={'Authorization': f'Bearer {k}'}, timeout=6)
        if r.json().get('value'):
            ok_.add(h)
    for h in miss[:20]:
        out[h] = h in ok_


def _dbc_oc(k, miss, out=None):
    """Offcloud /api/cache: marca qué magnets están ya en la caché común."""
    out = _DB_OK if out is None else out
    r = _session('offcloud.com').post(
        f'https://offcloud.com/api/cache?key={k}',
        data=[('urls[]', f'magnet:?xt=urn:btih:{h}') for h in miss[:40]], timeout=8)
    d = r.json()
    hits = set()
    if isinstance(d, dict):                           # {url|hash: {status:'cached'|...}}
        for kk, v in d.items():
            ok = (v.get('status') if isinstance(v, dict) else v) == 'cached' or v is True
            if ok:
                mh = re.search(r'btih:([0-9a-fA-F]{40})', str(kk))
                hits.add((mh.group(1) if mh else str(kk).rsplit(':')[-1]).lower())
    for h in miss[:40]:
        out[h] = h.lower() in hits


def _dbc_pio(k, miss, out=None):
    """Put.io no tiene instant-check: marca lo que ya está en la lista de
    transfers recientes (source=magnet con btih) — eso sí resuelve al instante."""
    out = _DB_OK if out is None else out
    r = _session('api.put.io').get('https://api.put.io/v2/transfers/list',
                                   headers={'Authorization': f'Bearer {k}'},
                                   params={'per_page': 100}, timeout=8)
    seen = set()
    for t in r.json().get('transfers', []):
        mh = re.search(r'btih:([0-9a-fA-F]{40})', str(t.get('source', '')))
        if mh and t.get('status') in ('COMPLETED', 'SEEDING'):
            seen.add(mh.group(1).lower())
    for h in miss[:40]:
        out[h] = h.lower() in seen


_DB_CHECK = {'real-debrid': _dbc_rd, 'torbox': _dbc_tb, 'alldebrid': _dbc_ad,
             'premiumize': _dbc_pm, 'easydebrid': _dbc_ed, 'debrid-link': _dbc_dl,
             'offcloud': _dbc_oc, 'putio': _dbc_pio}


def _db_avail_map(hashes):
    """{hash: provider} — multi-debrid: cada provider se consulta en paralelo
    (timeout propio; un provider lento no bloquea a los demás). El orden de
    configuración decide quién se sirve cuando varios lo tienen cacheado.
    _DB_OK[h] guarda el provider ganador (o False si ninguno lo tiene)."""
    if len(_DB_OK) > 5000:                                # infoHashes ilimitados:
        _DB_OK.clear()                                    # re-check es barato
    cfgs = _db_cfgs()
    miss = [h for h in hashes if h not in _DB_OK]
    if cfgs and miss:
        results: dict = {}

        def _one(p, k):
            fn = _DB_CHECK.get(p)
            scratch: dict = {}
            if fn:
                with suppress(requests.RequestException, TypeError, ValueError, KeyError):
                    fn(k, miss, scratch)
            results[p] = scratch

        with ThreadPoolExecutor(max_workers=len(cfgs)) as ex:
            list(ex.map(lambda c: _one(*c), cfgs))
        for p, _ in cfgs:                                 # orden cfg = prioridad
            for h in miss:
                if (results.get(p) or {}).get(h):
                    _DB_OK.setdefault(h, p)
        for h in miss:
            _DB_OK.setdefault(h, False)
    first = cfgs[0][0] if cfgs else None
    return {h: (_DB_OK[h] if isinstance(_DB_OK.get(h), str) else first)
            for h in hashes if _DB_OK.get(h)}


def _db_avail(hashes):
    """Hashes cacheados en algún debrid configurado -> set."""
    return set(_db_avail_map(hashes))


def _debrid_mark(streams):
    if not _db_cfgs():
        return
    hashes = [s.get('infoHash') for s in streams if s.get('infoHash')]
    if not hashes:
        return
    ok = _db_avail_map(hashes)
    for i, s in enumerate(streams):
        p = ok.get(s.get('infoHash'))
        if p:
            streams[i] = dict(s)                # copia: los dicts viven en _cache —
            _db_mark_one(p, streams[i])         # mutarlos envenena el caché con /r/


def _db_mark_one(p, s):
    """Stream cacheado -> rayo + url /r/ (o /dp/ con debrid_proxy: tunel de
    salida estilo StremThru — el cliente no toca el debrid, la IP visible es
    la del servidor; coste: nuestro ancho de banda). Multi-debrid: el tag
    [XX] identifica qué provider lo sirve."""
    h = s['infoHash']
    s['cached'] = True
    tag = f'[{_DB_SHORT.get(p, str(p).upper()[:4])}] ' if len(_db_cfgs()) > 1 else ''
    s['name'] = '⚡ ' + tag + (s.get('name') or '')
    route = 'dp' if CFG.get('debrid_proxy') else 'r'
    s['url'] = f'http://127.0.0.1:{PORT}/{route}/{p}/{h}?fi={s.get("fileIdx", 0)}'
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


def _db_oc(k, h, fi):
    """Offcloud: POST /api/cloud url=magnet -> requestId+url;
    GET /api/cloud/explore/{rid} -> ficheros -> url final (FenLight pattern)."""
    s = _session('offcloud.com')
    t = s.post(f'https://offcloud.com/api/cloud?key={k}',
               data={'url': f'magnet:?xt=urn:btih:{h}'}, timeout=15).json()
    if t.get('status') != 'downloaded' or not t.get('requestId'):   # aún en cola: no se sirve ahora
        return None
    base, rid = t.get('url', ''), t['requestId']                    # url = https://{srv}.offcloud.com/cloud/download/{rid}
    ex = s.get(f'https://offcloud.com/api/cloud/explore/{rid}',
               params={'key': k, 'format': 'detailed'}, timeout=10)
    try:
        items = ex.json()
    except ValueError:                                              # list/plaintext -> una ruta por línea
        items = [x.strip() for x in ex.text.splitlines() if x.strip()]
    files = []
    for it in items if isinstance(items, list) else []:
        u = it.get('url') if isinstance(it, dict) else str(it)
        if not u:
            continue
        full = u if u.startswith('http') else f'{base}/{u.lstrip("/")}'
        if str(it.get('filename', u) if isinstance(it, dict) else u).lower().endswith(_VIDEO_EXT):
            files.append(full)
    if not files and base:
        files = [f"{base}/{t.get('fileName', '')}"]                 # torrent mono-fichero
    return _pick_file(files, int(fi), files[0] if files else None)


def _db_pio(k, h, fi):
    """Put.io: transfers/add(url=magnet) -> files list -> /files/{id}/download."""
    s = _session('api.put.io')
    h_ = {'Authorization': f'Bearer {k}'}
    t = s.post('https://api.put.io/v2/transfers/add',
               data={'url': f'magnet:?xt=urn:btih:{h}'}, headers=h_, timeout=15).json()
    tr = t.get('transfer') or {}
    root = tr.get('file_id')                                        # dir raíz del transfer
    fs = s.get('https://api.put.io/v2/files/list',
               params={'parent_id': root or 0, 'per_page': 200},
               headers=h_, timeout=10).json().get('files') or []
    vids = [f for f in fs if str(f.get('name', '')).lower().endswith(_VIDEO_EXT)]
    fsel = vids[int(fi)] if int(fi) < len(vids) else \
        (max(vids, key=lambda f: f.get('size', 0)) if vids else None)
    if not fsel:
        return None
    return f'https://api.put.io/v2/files/{fsel["id"]}/download?oauth_token={k}'


_DB_RESOLVE = {'real-debrid': _db_rd, 'torbox': _db_tb, 'alldebrid': _db_ad,
               'premiumize': _db_pm, 'easydebrid': _db_ed, 'debrid-link': _db_dl,
               'offcloud': _db_oc, 'putio': _db_pio}


def _db_resolve(p, h, fi):
    """Resuelve infoHash -> URL HTTP final del debrid (redirect 302).
    La key se busca POR provider (multi-debrid: /r/torbox/ usa la key de torbox)."""
    k = _db_key(p)
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


def _db_usenet(nzb_url):
    """TorBox usenet: createusenetdownload -> mylist -> requestusenetdownload.
    Solo torbox sirve NZB — la key se busca entre TODOS los providers
    configurados (multi-debrid: torbox puede ser el segundo provider)."""
    k = _db_key('torbox')
    if not k:
        return None
    s = _session('api.torbox.app')
    h_ = {'Authorization': f'Bearer {k}'}
    try:
        t = s.post('https://api.torbox.app/v1/api/usenet/createusenetdownload',
                   data={'link': nzb_url}, headers=h_, timeout=15).json()
        uid = (t.get('data') or {}).get('usenetdownload_id')
        if not uid:
            return None
        m = s.get('https://api.torbox.app/v1/api/usenet/mylist',
                  headers=h_, timeout=10).json()
        item: dict = next((x for x in (m.get('data') or [])
                           if x.get('usenetdownload_id') == uid), {})
        fs = [f for f in item.get('files', [])
              if str(f.get('name', '')).lower().endswith(_VIDEO_EXT)]
        fsel = max(fs or item.get('files', []), key=lambda f: f.get('size', 0), default=None)
        q = {'token': k, 'usenet_id': uid}
        if fsel is not None and fsel.get('id') is not None:
            q['file_id'] = fsel['id']
        lnk = s.get('https://api.torbox.app/v1/api/usenet/requestusenetdownload',
                    params=q, headers=h_, timeout=10).json()
        d = lnk.get('data')
        return d.get('link') if isinstance(d, dict) else d
    except Exception:                                            # noqa: BLE001 — boundary: None, nunca crash
        return None


def _db_stream(p, h, fi, rq_headers):
    """Resuelve y abre el enlace debrid en modo stream (para /dp/). Reenvía los
    headers de validación del cliente (Range/If-*) — sin ellos el player no
    puede hacer seek. Devuelve el response requests o None."""
    u = _db_resolve(p, h, fi)
    if not u:
        return None
    try:
        s = _session(urllib.parse.urlsplit(u).hostname or 'debrid')
        fwd = {k: v for k, v in rq_headers.items()
               if k.lower() in ('range', 'if-none-match', 'if-modified-since')}
        return s.get(u, headers=fwd, stream=True, timeout=30)
    except Exception:                                            # noqa: BLE001 — boundary
        return None
