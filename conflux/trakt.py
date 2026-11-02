"""Trakt: device auth, refresh token, history/calendar y recomendaciones."""
# pylint: disable=broad-exception-caught,line-too-long
import threading
import time
from contextlib import suppress

import requests

from .cfg import _save_cfg
from .net import _session
from .state import _POOL_BG, CFG, log_ev

# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido



def _recom_seeds(hist):
    """slugs trakt de lo visto recientemente (None/'None' fuera)."""
    return [x for x in (str((e.get('movie') or e.get('show') or {}).get('ids', {}).get('trakt', ''))
                        for e in hist) if x and x != 'None']



def _recom_related(s, h, kind):
    def related(slug):
        try:
            return s.get(f'https://api.trakt.tv/{kind}/{slug}/related?limit=10',
                         headers=h, timeout=8).json() or []
        except (requests.RequestException, ValueError):
            return []
    return related




def _trakt_movies(days):
    """Estrenos de cine con fecha real via Trakt (client_id público, gratis).
    Sin CFG['trakt_client_id'] devuelve []."""
    cid = CFG.get('trakt_client_id')
    if not cid:
        return []
    try:
        r = _session('api.trakt.tv').get(
            f'https://api.trakt.tv/calendars/all/movies/{time.strftime("%Y-%m-%d")}/{days}',
            headers={'trakt-api-version': '2', 'trakt-api-key': cid}, timeout=8)
        out = []
        for e in r.json():
            mv = e.get('movie') or {}
            ids = mv.get('ids') or {}
            if not ids.get('imdb'):
                continue
            out.append({'id': 'tt' + str(ids['imdb']).lstrip('t'),
                        'imdb_id': 'tt' + str(ids['imdb']).lstrip('t'),
                        'type': 'movie', 'name': mv.get('title'),
                        'released': e.get('released') + 'T00:00:00.000Z',
                        'year': mv.get('year'),
                        'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img"})
        return out
    except Exception:                                            # noqa: BLE001 — boundary: catálogo nunca propagado
        return []



# ---- Trakt OAuth (device flow) — token personal para /calendars/my/* ----
_TRAKT_DEV = {}                    # device_code/interval/expires mientras autoriza


def _trakt_h(auth=False):
    h = {'trakt-api-version': '2', 'trakt-api-key': CFG.get('trakt_client_id', ''),
         'Content-Type': 'application/json'}
    if auth and CFG.get('trakt_access_token'):
        h['Authorization'] = f"Bearer {CFG['trakt_access_token']}"
    return h



def _trakt_device_start():
    """POST /oauth/device/code -> user_code+verification_url para autorizar una vez."""
    try:
        d = _session('api.trakt.tv').post('https://api.trakt.tv/oauth/device/code',
            json={'client_id': CFG['trakt_client_id']}, timeout=8).json()
        _TRAKT_DEV.clear()
        _TRAKT_DEV.update(d)
        _TRAKT_DEV['deadline'] = time.time() + d.get('expires_in', 600)
        threading.Thread(target=_trakt_device_poll, daemon=True).start()
        return d
    except (requests.RequestException, ValueError, TypeError):
        return None



def _trakt_device_poll():
    """Poll /oauth/device/token hasta que el usuario autoriza (o expira)."""
    while time.time() < _TRAKT_DEV.get('deadline', 0):
        time.sleep(max(3, _TRAKT_DEV.get('interval', 5)))
        try:
            r = _session('api.trakt.tv').post('https://api.trakt.tv/oauth/device/token',
                json={'code': _TRAKT_DEV['device_code'],
                      'client_id': CFG['trakt_client_id'],
                      'client_secret': CFG['trakt_client_secret']}, timeout=8)
            d = r.json()
            if d.get('access_token'):
                CFG['trakt_access_token']  = d['access_token']
                CFG['trakt_refresh_token'] = d.get('refresh_token', '')
                CFG['trakt_expires'] = time.time() + d.get('expires_in', 7776000)
                _save_cfg()
                _TRAKT_DEV.clear()
                log_ev('trakt_auth', ok=True)
                return
        except (requests.RequestException, ValueError) as e:
            log_ev('trakt_auth', ok=False, err=repr(e)[:100])
    _TRAKT_DEV.clear()



def _trakt_refresh():
    """Renueva el access_token si caduca en <10min (grant refresh_token)."""
    if not CFG.get('trakt_refresh_token'):
        return
    if time.time() < CFG.get('trakt_expires', 0) - 600:
        return
    try:
        d = _session('api.trakt.tv').post('https://api.trakt.tv/oauth/token',
            json={'refresh_token': CFG['trakt_refresh_token'],
                  'client_id': CFG['trakt_client_id'],
                  'client_secret': CFG['trakt_client_secret'],
                  'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
                  'grant_type': 'refresh_token'}, timeout=8).json()
        if d.get('access_token'):
            CFG['trakt_access_token']  = d['access_token']
            CFG['trakt_refresh_token'] = d.get('refresh_token', CFG['trakt_refresh_token'])
            CFG['trakt_expires'] = time.time() + d.get('expires_in', 7776000)
            _save_cfg()
    except (requests.RequestException, ValueError, OSError) as e:
        log_ev('trakt_token', ok=False, err=repr(e)[:100])



def _trakt_my(days):
    """Calendario PERSONAL: shows + movies del usuario (requiere token OAuth).
    -> (eps[(tt, season, ep, when, show_meta)], films[{meta-like}])."""
    if not CFG.get('trakt_access_token'):
        return [], []
    _trakt_refresh()
    today = time.strftime('%Y-%m-%d')
    s = _session('api.trakt.tv')
    h = _trakt_h(auth=True)
    eps, films = [], []
    eps = _trakt_my_eps(s, h, today, days)
    films = _trakt_my_films(s, h, today, days)
    return eps, films



def _trakt_my_eps(s, h, today, days):
    """(tt, season, ep, when, show_meta) de series seguidas."""
    eps = []
    with suppress(requests.RequestException, TypeError, ValueError, KeyError, AttributeError):
        for e in s.get(f'https://api.trakt.tv/calendars/my/shows/{today}/{days}',
                       headers=h, timeout=8).json():
            sh = e.get('show') or {}
            ids = sh.get('ids') or {}
            ep = e.get('episode') or {}
            if not ids.get('imdb'):
                continue
            eps.append(('tt' + str(ids['imdb']).lstrip('t'), ep.get('season', 1),
                        ep.get('number', 1), e.get('first_aired'),
                        {'id': 'tt' + str(ids['imdb']).lstrip('t'), 'type': 'series',
                         'name': sh.get('title'), 'year': sh.get('year'),
                         'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img",
                         '_my': True}))
    return eps



def _trakt_my_films(s, h, today, days):
    """metas-like de pelis en la watchlist."""
    films = []
    with suppress(requests.RequestException, TypeError, ValueError, KeyError, AttributeError):
        for e in s.get(f'https://api.trakt.tv/calendars/my/movies/{today}/{days}',
                       headers=h, timeout=8).json():
            mv = e.get('movie') or {}
            ids = mv.get('ids') or {}
            if not ids.get('imdb'):
                continue
            tt = 'tt' + str(ids['imdb']).lstrip('t')
            films.append({'id': tt, 'imdb_id': tt, 'type': 'movie', 'name': mv.get('title'),
                          'released': (e.get('released') or '')[:10] + 'T00:00:00.000Z',
                          'year': mv.get('year'),
                          'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img"})
    return films



def _recom_add(lst, utype, seen, out):
    """Metas related dedupe por imdb -> out (máx 40). Devuelve len(out)."""
    for it in lst:
        ids = it.get('ids') or {}
        if not ids.get('imdb') or ids['imdb'] in seen:
            continue
        seen.add(ids['imdb'])
        out.append({'id': 'tt' + str(ids['imdb']).lstrip('t'),
                    'type': utype, 'name': it.get('title'), 'year': it.get('year'),
                    'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img"})
        if len(out) >= 40:
            break
    return len(out)



def _recom(utype):
    """'Porque viste X' (4.5): historial reciente Trakt -> /related por item,
    dedup por imdb. Solo con token OAuth."""
    if not CFG.get('trakt_access_token'):
        return {'metas': []}
    _trakt_refresh()
    kind = 'movies' if utype == 'movie' else 'shows'
    s = _session('api.trakt.tv')
    h = _trakt_h(auth=True)
    try:
        hist = s.get(f'https://api.trakt.tv/users/me/history/{kind}?limit=6',
                     headers=h, timeout=8).json()
    except (requests.RequestException, ValueError):
        return {'metas': []}
    seeds = _recom_seeds(hist)
    related = _recom_related(s, h, kind)
    out = []
    seen = set()
    for lst in _POOL_BG.map(related, seeds):
        if _recom_add(lst, utype, seen, out) >= 40:
            break
    return {'metas': out}
