"""OpenSubtitles REST: query, mapping ES-first y redirect /s/<id>."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import requests

from conflux.core.cfg import _cfgget
from conflux.core.state import PORT, as_dict
from conflux.domain.sources import _resolve_sid
from conflux.infra.net import _session


def _subs_query(stype, sid):
    """sid -> params OpenSubtitles (imdb + S:E si serie) o None."""
    parts = sid.split(':')
    imdb = parts[0]
    if not imdb.startswith('tt'):
        imdb = _resolve_sid(stype, sid).split(':')[0]
    if not imdb.startswith('tt'):
        return None
    q = {'imdb_id': imdb.lstrip('t'), 'languages': 'es,en'}
    if stype == 'series' and len(parts) > 2:
        q['season_number'], q['episode_number'] = parts[1], parts[2]
    return q


def _subs_map(r):
    """respuesta OpenSubtitles -> subs con url /s/<file_id>, ES-first."""
    out = []
    for d in as_dict(r.json()).get('data') or []:
        a = d.get('attributes') or {}
        fid = ((a.get('files') or [{}])[0]).get('file_id')
        if not fid:
            continue
        out.append({'id': str(fid), 'lang': a.get('language') or 'es',
                    'url': f'http://127.0.0.1:{PORT}/s/{fid}'})
    out.sort(key=lambda s: 0 if s['lang'].startswith('es') else 1)   # ES-first  # pragma: no mutate — clave binaria, 1/2 equivalente
    return out[:20]


def handle_subtitles(stype, sid):
    """OpenSubtitles REST (3.3): ES/LATAM primero, resto después.
    sid = tt123[:season[:ep]]. Cada sub apunta a /s/<file_id> (redirect al CDN)."""
    key = _cfgget('opensubtitles_key')
    q = _subs_query(stype, sid) if key else None
    if not q:
        return {'subtitles': []}
    try:
        r = _session('api.opensubtitles.com').get(
            'https://api.opensubtitles.com/api/v1/subtitles',
            params=q, headers={'Api-Key': key, 'User-Agent': 'conflux 1.0'}, timeout=8)
        return {'subtitles': _subs_map(r)}
    except Exception:                                            # noqa: BLE001 — boundary HTTP: siempre respuesta válida
        return {'subtitles': []}


_OS_TOK: dict = {}        # token JWT OpenSubtitles + ts (login ~24h)


def _os_login():
    """Login opensubtitles.com -> JWT (Bearer). /download exige cuenta de usuario:
    la Api-Key pública sirve para /subtitles (búsqueda) pero /download responde
    503/403 sin Bearer propio. Cache 23h; caduca -> reloguea en el próximo /s/."""
    key = _cfgget('opensubtitles_key')
    user, pw = _cfgget('opensubtitles_user'), _cfgget('opensubtitles_pass')
    if not (key and user and pw):
        return None
    import time
    if _OS_TOK.get('token') and time.time() - _OS_TOK.get('ts', 0) < 23 * 3600:
        return _OS_TOK['token']
    try:
        d = _session('api.opensubtitles.com').post(
            'https://api.opensubtitles.com/api/v1/login',
            json={'username': user, 'password': pw},
            headers={'Api-Key': key, 'User-Agent': 'conflux 1.0'}, timeout=8).json()
        tok = d.get('token')
        if tok:
            _OS_TOK['token'], _OS_TOK['ts'] = tok, time.time()
        return tok
    except (requests.RequestException, ValueError):
        return None


def _sub_download(file_id):
    """file_id -> URL CDN (POST /download) para redirect 302.
    Con cuenta: Bearer propio (cuota diaria del usuario). Sin cuenta: Api-Key
    pública (suele dar 503 — el endpoint realmente exige login)."""
    key = _cfgget('opensubtitles_key')
    if not key:
        return None
    h = {'Api-Key': key, 'User-Agent': 'conflux 1.0'}
    tok = _os_login()
    if tok:
        h['Authorization'] = f'Bearer {tok}'
    try:
        return (_session('api.opensubtitles.com').post(
            'https://api.opensubtitles.com/api/v1/download', json={'file_id': int(file_id)},
            headers=h, timeout=8)
            .json().get('link'))
    except (requests.RequestException, ValueError):
        return None
