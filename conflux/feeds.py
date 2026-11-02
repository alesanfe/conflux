"""Integraciones de contenido: Trakt, AniList, Wikidata, meta-fix, recomendaciones."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import threading
import time
from contextlib import suppress
from string import Template

import requests

from .cfg import _save_cfg
from .net import _session, fetch
from .state import _POOL, _POOL_BG, _YEARS, CFG, SOURCES, log_ev


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


def _pv(claims, prop):
    for s in claims.get(prop, []):
        v = s.get('mainsnak', {}).get('datavalue', {}).get('value')
        if v is not None:
            return v
    return None


_wd_c = {}                                    # yt -> (ts, dict) — TTL 24h: no cambia a diario


def _wikidata(yt):
    """Canal yt -> datos reales de Wikidata (country/website/socials/year) por P2397."""
    ent = _wd_c.get(yt)
    if ent and time.time() - ent[0] < 86400:
        return ent[1]
    out = {}
    d = fetch(f'https://www.wikidata.org/w/api.php?action=query&list=search'
              f'&srsearch=haswbstatement:P2397={yt}&format=json&srlimit=1', 6)
    q = ((d or {}).get('query', {}).get('search') or [{}])[0].get('title')
    if q:
        c = (fetch(f'https://www.wikidata.org/wiki/Special:EntityData/{q}.json', 8) or {}
             ).get('entities', {}).get(q, {}).get('claims', {})
        out = _wd_claims_out(c)
    _wd_c[yt] = (time.time(), out)
    return out


_WD_SOCIAL = (('P2002', 'X', 'https://x.com/'),
              ('P2003', 'Instagram', 'https://instagram.com/'),
              ('P2013', 'Facebook', 'https://facebook.com/'))


def _wd_country(c):
    """P17/P495 -> label 'en' del país (2ª entidad)."""
    cq = _pv(c, 'P17') or _pv(c, 'P495')
    if not isinstance(cq, dict):
        return None
    d2 = fetch(f'https://www.wikidata.org/wiki/Special:EntityData/{cq["id"]}.json', 6)
    return ((d2 or {}).get('entities', {}).get(cq['id'], {})
            .get('labels', {}).get('en', {}).get('value'))


def _wd_claims_out(c):
    """claims wikidata -> {country, website, year, social[]}."""
    out = {}
    co = _wd_country(c)
    if co:
        out['country'] = co
    web = _pv(c, 'P856')
    if isinstance(web, str):
        out['website'] = web
    inc = _pv(c, 'P571')
    if isinstance(inc, dict) and inc.get('time'):
        out['year'] = inc['time'][1:5]
    for p, label, base in _WD_SOCIAL:
        h = _pv(c, p)
        if isinstance(h, str):
            out.setdefault('social', []).append(
                {'name': label, 'category': 'social', 'url': base + h.lstrip('@')})
    return out


_YT_AWARDS = [(100e6, 'Red Diamond'), (50e6, 'Ruby'), (10e6, 'Diamond'),
              (1e6, 'Gold'), (1e5, 'Silver')]


def _fmt_num(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    for u, d in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if n >= d:
            return f'{n/d:.1f}{u}'
    return str(int(n))


def _fix_meta_desc(m):
    """Descripción vacía -> stats reales (subs/views/género)."""
    parts = []
    subs, vc = _fmt_num(m.get('popularity')), _fmt_num(m.get('viewCount'))
    if subs:
        parts.append(f'{subs} suscriptores')
    if vc:
        parts.append(f'{vc} reproducciones')
    g = m.get('genre')
    if g:
        parts.append(' · '.join(g[:3]) if isinstance(g, list) else str(g))
    m['description'] = 'Canal de YouTube' + (' · ' + ' · '.join(parts) if parts else '')


def _fix_meta_poster(m, yt):
    """Avatar ggpht caducado -> unavatar; logo ≈ avatar si falta."""
    p = m.get('poster') or ''
    if 'ggpht.com' in p or 'googleusercontent.com' in p:
        m['poster'] = f'https://unavatar.io/youtube/{yt}'
    if not m.get('logo') and m.get('poster'):
        m['logo'] = m['poster']                                   # canal: logo ≈ avatar


def _fix_meta_fields(m, yt):
    """poster/keywords/logo del canal — rellena solo lo que falta."""
    _fix_meta_poster(m, yt)
    if not (m.get('description') or '').strip():
        _fix_meta_desc(m)
    if not (m.get('keywords') or '').strip() and m.get('name'):
        m['keywords'] = m['name']


def _fix_meta_basic(m, yt):
    """Avatar ggpht caducado -> unavatar; desc/keywords/logo/awards por subs."""
    _fix_meta_fields(m, yt)
    _fix_meta_awards(m)


def _fix_meta_awards(m):
    """Premios reales por nº de suscriptores (tabla _YT_AWARDS)."""
    try:
        subs_n = int(float(m.get('popularity') or 0))
    except (TypeError, ValueError):
        subs_n = 0
    if not m.get('awards'):
        aw = next((f'YouTube {n} Creator Award' for t, n in _YT_AWARDS if subs_n >= t), None)
        if aw:
            m['awards'] = aw                                   # premios reales por subs


def _fix_meta_wd(m, wd):
    """Wikidata -> country/releaseInfo/links (solo campos vacíos)."""
    if wd.get('country') and not m.get('country'):
        m['country'] = wd['country']
    if wd.get('year') and not m.get('releaseInfo'):
        m['releaseInfo'] = wd['year']
    if not m.get('links'):
        links = ([{'name': 'Web oficial', 'category': 'official', 'url': wd['website']}]
                 if wd.get('website') else []) + wd.get('social', [])
        if links:
            m['links'] = links


def _fix_meta_vids(m, vids, dead):
    """backdrop/trailer del último vídeo + poda de vídeos 404."""
    first = str(vids[0].get('id') or '').rsplit(':', 1)[-1]
    if first:
        if not m.get('background'):                # backdrop = thumb del último vídeo
            m['background'] = f'https://i.ytimg.com/vi/{first}/hqdefault.jpg'
        if not m.get('unsubscribedTrailer'):       # trailer = último vídeo
            m['unsubscribedTrailer'] = first
    if dead:
        m['videos'] = [v for v in vids if v.get('id') not in dead]


def _fix_meta_deep(m, yt):
    """Detalle: Wikidata (país/web/social/año) + backdrop/trailer + vídeos muertos."""
    vids = m.get('videos')
    fw = _POOL.submit(_wikidata, yt)
    fd = _POOL.submit(_dead_videos, vids) if vids else None   # en paralelo con wikidata
    _fix_meta_wd(m, fw.result())
    if vids:
        _fix_meta_vids(m, vids, fd.result())


def _fix_metas(d, deep=False):
    """Canal yt: avatar ggpht caducado -> unavatar; descripción vacía -> stats reales;
    deep=True (detalle): + Wikidata (país, web, social, año), awards por subs, logo, vídeos muertos."""
    ms = d.get('metas') or ([d['meta']] if isinstance(d.get('meta'), dict) else [])
    for m in ms:
        yt = m.get('yt_id')
        i = str(m.get('id') or '')
        if not yt and i.startswith('yt_id:'):
            yt = i[6:]
        if not yt:
            continue
        _fix_meta_basic(m, yt)
        if deep:
            _fix_meta_deep(m, yt)
    return d


def _dead_videos(vids):
    """Vídeos borrados/privados: todos sus thumbs 404 -> fuera del meta (fichas rotas)."""
    def chk(v):
        vid = str(v.get('id') or '').rsplit(':', 1)[-1]
        if not vid:
            return None
        try:
            r = _session('i.ytimg.com').head(f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', timeout=4)
            return v.get('id') if r.status_code != 200 else None
        except requests.RequestException:
            return None
    fs = [_POOL.submit(chk, v) for v in vids[:40]]               # primera página visible
    return {f.result() for f in fs if f.result()}


def _anilist_row(a):
    """airingSchedule -> {ep, airingAt, idMal, img, title} o None sin idMal."""
    m = a.get('media') or {}
    if not m.get('idMal'):
        return None
    return {'ep': a.get('episode'), 'airingAt': a.get('airingAt'),
            'idMal': m['idMal'], 'img': (m.get('coverImage') or {}).get('large'),
            'title': (m.get('title') or {}).get('english')
                     or (m.get('title') or {}).get('romaji')}


def _anilist_airing(days):
    """Próximas emisiones de anime (AniList GraphQL, sin auth):
    [{ep, airingAt, idMal, title, img}] ordenadas por fecha."""
    out, seen = [], set()
    t_end = int(time.time()) + days * 86400
    for page in (1, 2, 3):
        q = {'query': Template('{Page(page:$page,perPage:50){airingSchedules(notYetAired:true,'
                               'airingAt_lesser:$t_end,sort:TIME){airingAt episode media{idMal '
                               'title{romaji english} coverImage{large}}}}}')
             .substitute(page=page, t_end=t_end)}           # Template: las llaves GraphQL chocan con f-string
        try:
            r = _session('graphql.anilist.co').post('https://graphql.anilist.co',
                json=q, timeout=8).json()
        except (requests.RequestException, ValueError):
            break
        rows = ((r.get('data') or {}).get('Page') or {}).get('airingSchedules') or []
        if not rows:
            break
        for a in rows:
            row = _anilist_row(a)
            if not row or (row['idMal'], row['ep']) in seen:
                continue
            seen.add((row['idMal'], row['ep']))
            out.append(row)
    return out



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


def tmdb_year():
    """Año actual (etiqueta/picker de los catálogos de estrenos)."""
    tmdb = next(b for n, b in SOURCES if n == 'TMDB')
    return f"{tmdb}/catalog/movie/tmdb.year/genre={_YEARS[0]}.json"
