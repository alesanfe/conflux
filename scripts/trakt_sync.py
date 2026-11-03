#!/usr/bin/env python3
"""Sincroniza lo visto en Stremio -> historial Trakt (idempotente).

Lee el localStorage de Stremio (leveldb en Win/macOS/Linux o sqlite en
flatpak), decodifica los bitmaps `watched` (zlib+base64 por índice de vídeo)
y marca en Trakt los episodios/películas realmente terminados. Solo añade lo
que falte: consulta primero /users/me/history y no repite episodios ya
registrados.

Necesita el proxy en marcha (127.0.0.1:9700) para resolver ids y videos, y
config.json con trakt_access_token (scripts/trakt_auth.py).

Uso:  .venv/Scripts/python scripts/trakt_sync.py
"""
# pylint: disable=broad-exception-caught
import base64
import glob
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = 'http://127.0.0.1:9700'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'src'))

_WRE = re.compile(rb'lib_([A-Za-z0-9]+)_([A-Za-z0-9:_-]+)')
_WB = re.compile(rb'"watched":"([A-Za-z0-9:_-]+):(\d+):(\d+):([A-Za-z0-9+/=]{6,})"')


def _leveldb_files():
    home = os.path.expanduser('~')
    la = os.environ.get('LOCALAPPDATA', os.path.join(home, 'AppData', 'Local'))
    ra = os.environ.get('APPDATA', os.path.join(home, 'AppData', 'Roaming'))
    bases = [os.path.join(la, 'Smart Code ltd', 'Stremio'),
             os.path.join(ra, 'stremio'), os.path.join(ra, 'stremio5'),
             os.path.join(home, 'Library', 'Application Support',
                          'Smart Code ltd', 'Stremio'),
             os.path.join(home, 'Library', 'Application Support', 'stremio'),
             os.path.join(home, '.local', 'share', 'Smart Code ltd', 'Stremio'),
             os.path.join(home, '.config', 'stremio')]
    files = []
    for b in bases:
        for d in glob.glob(os.path.join(b, '**', 'Local Storage', 'leveldb'),
                           recursive=True):
            files += glob.glob(os.path.join(d, '*.log')) + \
                     glob.glob(os.path.join(d, '*.ldb'))
    return sorted(files, key=os.path.getmtime, reverse=True)


def _watched_bitmap():
    """{meta_id: [indices de video vistos]} del leveldb (último write gana)."""
    out = {}
    for f in _leveldb_files():
        try:
            d = open(f, 'rb').read()
        except OSError:
            continue
        for m in _WB.finditer(d):
            iid, _a, _b, b64 = m.group(1).decode(), m.group(2), m.group(3), m.group(4)
            try:
                raw = zlib.decompress(base64.b64decode(b64))
            except Exception:
                continue
            bits = [i for i in range(len(raw) * 8)
                    if raw[i // 8] & (1 << (i % 8))]
            out[iid] = bits                          # el último mtime gana
        if out:
            break                                     # el fichero más nuevo basta
    return out


def _get(path, timeout=15):
    return json.load(urllib.request.urlopen(f'{PROXY}{path}', timeout=timeout))


def _tt(meta_id, utype):
    """meta id cualquiera -> tt via el propio proxy (/meta devuelve imdb_id)."""
    q = urllib.parse.quote(meta_id, safe='')
    try:
        m = _get(f'/meta/{utype}/{q}.json').get('meta') or {}
    except Exception:
        return None
    return m.get('imdb_id') or (meta_id if meta_id.startswith('tt') else None)


def _episodes_of(meta_id, utype, idxs):
    """indices del bitmap -> [(season, episode)] usando los videos del meta."""
    q = urllib.parse.quote(meta_id, safe='')
    try:
        m = _get(f'/meta/{utype}/{q}.json').get('meta') or {}
    except Exception:
        return []
    vids = m.get('videos') or []
    out = []
    for i in idxs:
        if i < len(vids):
            s_no, e_no = vids[i].get('season'), vids[i].get('episode')
            if s_no is not None and e_no is not None:
                out.append((s_no, e_no))
    return out


def main():
    import proxy                                   # noqa: F401 — carga cfg
    from conflux.core.state import CFG
    if not CFG.get('trakt_access_token'):
        sys.exit('sin trakt_access_token — ejecuta scripts/trakt_auth.py')
    from conflux.infra.net import _session
    from conflux.infra.trakt import _trakt_h

    watched = _watched_bitmap()
    if not watched:
        sys.exit('sin datos de ' 'watched' ' en localStorage de Stremio')
    print(f'{len(watched)} items con episodios vistos: {list(watched)}')

    s = _session('api.trakt.tv')
    h = _trakt_h(auth=True)
    try:
        hist = s.get('https://api.trakt.tv/users/me/history/episodes?limit=2000',
                     headers=h, timeout=15).json()
        have = {(e['episode']['season'], e['episode']['number'],
                 e['show']['ids'].get('imdb')) for e in hist if e.get('episode')}
    except Exception as e:
        sys.exit(f'history trakt: {e}')

    shows, movies = {}, []
    for meta_id, idxs in watched.items():
        utype = 'series' if ':' in meta_id else 'movie'
        # heurística: los ids con ':' pueden ser movie también; lo decide el meta
        tt = _tt(meta_id, utype)
        if not tt:
            print(f'  {meta_id}: sin imdb, omitido')
            continue
        if 'kitsu' in meta_id or 'mal' in meta_id or 'anilist' in meta_id:
            utype = 'series'                        # anime siempre series
        eps = _episodes_of(meta_id, utype, idxs)
        if eps:
            new = [(s_no, e_no) for s_no, e_no in eps
                   if (s_no, e_no, tt) not in have]
            if new:
                shows.setdefault(tt, set()).update(new)
        else:
            movies.append(tt)                       # item sin videos: peli/item suelto

    payload = {'shows': [{'ids': {'imdb': tt},
                        'seasons': [{'number': s_no,
                                     'episodes': [{'number': e_no}
                                                  for s2, e_no in sorted(eps)
                                                  if s2 == s_no]}
                                    for s_no in sorted({s for s, _ in eps})]}
                       for tt, eps in shows.items()],
               'movies': [{'ids': {'imdb': tt}} for tt in movies]}
    payload['shows'] = [x for x in payload['shows'] if x['seasons']]
    if not payload['shows'] and not payload['movies']:
        print('todo sincronizado ya — nada que añadir')
        return
    r = s.post('https://api.trakt.tv/sync/history', json=payload,
               headers=h, timeout=15)
    try:
        res = r.json()
        added = res.get('added', {})
        print(f"trakt sync: +{added.get('episodes', 0)} episodios, "
              f"+{added.get('movies', 0)} pelis (http {r.status_code})")
    except ValueError:
        print(f'trakt sync: http {r.status_code}')


if __name__ == '__main__':
    main()
