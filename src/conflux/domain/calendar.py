"""Feed sintético de estrenos para el calendario de Stremio."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import time
import urllib.parse
import urllib.request

from conflux.core.cfg import _cfgget
from conflux.core.state import _POOL_BG, _YEARS, CFG, SOURCES, as_dict
from conflux.domain.lists import tmdb_year
from conflux.domain.sources import _resolve_sid
from conflux.infra.anilist import _anilist_airing
from conflux.infra.net import fetch
from conflux.infra.trakt import _trakt_movies, _trakt_my


def _cal_add_film(m, hoy, seen, out):
    """Peli con fecha de estreno FUTURA -> meta 'series' + vídeo sintético
    (el pipeline del calendario solo entiende episodios)."""
    if not isinstance(m, dict) or not m:
        return
    if m.get('imdb_id'):
        m['id'] = m['imdb_id']              # canonical tt: dedup + ficha
    rel = (m.get('released') or '')[:10]
    if not rel or rel <= hoy or m['id'] in seen:
        return
    seen.add(m['id'])
    m = dict(m)
    m['type'] = 'series'   # el pipeline del calendario solo
    m['videos'] = [{'id': f"{m['id']}:1:1", 'name': 'Estreno en cines',
                    'season': 1, 'episode': 1, 'number': 1,
                    'released': m['released'], 'firstAired': m['released'],
                    'thumbnail': m.get('poster'), 'overview': m.get('description') or ''}]
    out.append(m)


def _base(name):
    """Base URL de una fuente por nombre."""
    return next(b for n, b in SOURCES if n == name)


def _cal_seed_ids(cine, kitsu, anise):
    """Series populares en emisión: top cinemeta + temporada AniSeason (ya tt)
    + long-runners kitsu airing resueltos a tt. Devuelve también los futures
    de pelis para no refetchear."""
    fs_top = _POOL_BG.submit(fetch, f"{cine}/catalog/series/top.json", 10)
    fs_air = _POOL_BG.submit(fetch, f"{kitsu}/catalog/anime/kitsu-anime-airing.json", 10)
    fs_sea = _POOL_BG.submit(fetch, f"{anise}/catalog/series/latest_anime_seasons.json", 12)
    fs_amv = _POOL_BG.submit(fetch, f"{anise}/catalog/movie/latest_anime_seasons.json", 12)
    fs = [_POOL_BG.submit(fetch, f"{cine}/catalog/movie/year/genre={y}"
                       f"{'&skip='+str(s) if s else ''}.json", 10)
          for y in (_YEARS[0], str(int(_YEARS[0]) + 1)) for s in (0, 50, 100)]
    td = as_dict(fetch(tmdb_year(), 10))
    return _cal_top_ids(fs_top, fs_sea, fs_air), fs, fs_amv, td


def _cal_top_ids(fs_top, fs_sea, fs_air):
    """top cinemeta + temporada + kitsu airing -> ids tt deduplicados."""
    top_ids = _cat_ids(fs_top, 'tt')[:30]
    top_ids += [i for i in _cat_ids(fs_sea, 'tt') if i not in top_ids]
    kids = _cat_ids(fs_air)[:20]
    top_ids += [r for r in _POOL_BG.map(lambda i: _resolve_sid('series', i), kids)
                if r.startswith('tt') and r not in top_ids]
    return top_ids


def _cat_ids(fut, prefix=''):
    """ids de un catálogo future -> lista (filtrable por prefijo, p.ej. 'tt')."""
    return [m['id'] for m in as_dict(fut.result()).get('metas') or []
            if isinstance(m, dict) and str(m.get('id', '')).startswith(prefix)]


def _cal_series_eps(cine, top_ids, seen, out):
    """calendar-videos upstream: TODOS los episodios fechados de los ids semilla."""
    if not top_ids:
        return
    cv = as_dict(fetch(f"{cine}/catalog/series/calendar-videos/calendarVideosIds={','.join(top_ids)}.json", 12))
    for m in cv.get('metasDetailed') or []:
        if isinstance(m, dict) and m.get('id') and m['id'] not in seen and m.get('videos'):
            seen.add(m['id'])
            out.append(m)


def _cal_year_films(fs, hoy, seen, out):
    for f in fs:
        for m in as_dict(f.result()).get('metas') or []:
            _cal_add_film(m, hoy, seen, out)


def _cal_meta_films(tmdb, ids, hoy, seen, out):
    """Pelis sin fecha en el catálogo: TMDB meta por imdb_id da 'released'."""
    fs3 = [_POOL_BG.submit(fetch, f"{tmdb}/meta/movie/{urllib.parse.quote(i, safe='')}.json", 10)
           for i in ids]
    for f in fs3:
        m = as_dict(f.result()).get('meta')
        if m and m.get('imdb_id'):
            m['id'] = m['imdb_id']
        _cal_add_film(m, hoy, seen, out)


def _cal_films(tmdb, fs, fs_amv, td, hoy, seen, out):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Pelis del año: cinemeta year (fecha en el propio meta) + TMDB year
    (meta por imdb_id) + anime de temporada + cinemeta sin fecha resueltas
    vía TMDB."""
    _cal_year_films(fs, hoy, seen, out)
    _cal_meta_films(tmdb, [m['id'] for m in td.get('metas') or []], hoy, seen, out)
    _cal_meta_films(tmdb, _cal_bare_ids(fs, fs_amv), hoy, seen, out)

def _cal_bare_ids(fs, fs_amv):
    """ids tt sin fecha de estreno (anime temporada + cinemeta year sin released)."""
    bare = [m['id'] for f in fs for m in as_dict(f.result()).get('metas') or []
            if isinstance(m, dict) and not (m.get('released') or '')[:10]
            and m.get('id', '').startswith('tt')][:40]
    return _cat_ids(fs_amv, 'tt') + bare


def _cal_anime_video(tt, a, m):
    return {'id': f"{tt}:1:{a['ep']}", 'name': a.get('title') or f"Episodio {a['ep']}",
            'season': 1, 'episode': a['ep'], 'number': a['ep'],
            'released': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(a['airingAt'])),
            'firstAired': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(a['airingAt'])),
            'thumbnail': a.get('img') or m.get('poster')}


def _cal_ensure_meta(tt, m, metas_tt, seen, out):
    """Devuelve el meta de tt con 'videos' inicializado, registrándolo en
    out/seen si es nuevo. None si el upstream no lo tiene."""
    if tt in metas_tt:
        return metas_tt[tt]
    if not m or not m.get('id'):
        return None
    m = dict(m)
    m.setdefault('videos', [])
    m['id'] = tt
    metas_tt[tt] = m
    out.append(m)
    seen.add(tt)
    return m


def _anime_eps_map(al):
    """AiringSchedules -> {tt: [a,...]} resolviendo mal->tt en pool."""
    an_ids = [a['idMal'] for a in al if a.get('idMal')]
    tts = list(_POOL_BG.map(lambda i: _resolve_sid('series', f'mal:{i}'), an_ids))
    by: dict = {}
    for a, tt in zip(al, tts, strict=True):
        if str(tt).startswith('tt'):
            by.setdefault(tt, []).append(a)
    return by


def _cal_anime_eps(cine, days, seen, out):
    """Anime con fecha REAL de emisión via AniList (gratis, sin key):
    AiringSchedule próximos N días -> episodios exactos, resueltos mal->tt."""
    eps_by_tt = _anime_eps_map(_anilist_airing(days))
    metas_tt = {m['id']: m for m in out}
    fmeta = [_POOL_BG.submit(fetch, f"{cine}/meta/series/{i}.json", 10) for i in eps_by_tt]
    for (tt, eps), f in zip(eps_by_tt.items(), fmeta, strict=True):
        _cal_anime_fill(tt, eps, metas_tt.get(tt) or as_dict(f.result()).get('meta'),
                        metas_tt, seen, out)

def _cal_anime_fill(tt, eps, meta_raw, metas_tt, seen, out):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Un anime: asegura meta en out y añade los episodios que falten."""
    m = _cal_ensure_meta(tt, meta_raw, metas_tt, seen, out)
    if not m:
        return
    have = {v['id'] for v in m.get('videos', [])}
    m['videos'] += [_cal_anime_video(tt, a, m) for a in eps
                    if f"{tt}:1:{a['ep']}" not in have]


def _cal_trakt(cine, days, hoy, seen, out):
    """Estrenos reales via Trakt (client_id gratis en config) + calendario
    PERSONAL (token OAuth): episodios de series seguidas y pelis watchlist."""
    for mv in _trakt_movies(days):
        _cal_add_film(mv, hoy, seen, out)
    my_eps, my_films = _trakt_my(days)
    for mv in my_films:
        _cal_add_film(mv, hoy, seen, out)
    _cal_my_eps(cine, my_eps, seen, out)


def _cal_my_eps(cine, my_eps, seen, out):
    """Episodios personales Trakt -> metas (cinemeta o show-meta de fallback)."""
    metas_tt = {m['id']: m for m in out}                    # reindexed tras add_film
    new_tt = sorted({tt for tt, *_ in my_eps if tt not in metas_tt})
    _cal_my_fill(cine, new_tt, my_eps, metas_tt, seen, out)
    _my_eps_videos(metas_tt, my_eps)

def _cal_my_fill(cine, new_tt, my_eps, metas_tt, seen, out):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Resuelve (paralelo) el meta de cada serie nueva; fallback al show-meta de Trakt."""
    fallback = {tt: sm for tt, _, _, _, sm in my_eps}
    fmy = [_POOL_BG.submit(fetch, f"{cine}/meta/series/{i}.json", 10) for i in new_tt]
    for tt, f in zip(new_tt, fmy, strict=True):
        _cal_ensure_meta(tt, as_dict(f.result()).get('meta') or fallback.get(tt),
                         metas_tt, seen, out)


def _my_eps_videos(metas_tt, my_eps):
    """Añade el vídeo 'en tu Trakt' a cada meta resuelta (dedup por vid)."""
    for tt, s_no, e_no, when, _ in my_eps:
        m = metas_tt.get(tt)
        if not m:
            continue
        vid = f"{tt}:{s_no}:{e_no}"
        if vid not in {v['id'] for v in m.get('videos', [])}:
            m['videos'].append({'id': vid, 'name': f"S{s_no}E{e_no} · en tu Trakt",
                                'season': s_no, 'episode': e_no, 'number': e_no,
                                'released': when, 'firstAired': when,
                                'thumbnail': m.get('poster')})


def _estrenos_cal(_extra=None):
    """Calendario completo = series populares en emisión (episodios reales vía
    cinemeta calendar-videos con ids sembrados del top) + pelis del año no
    estrenadas (vídeo sintético = fecha de estreno).
    extra se ignora a propósito: el cliente pide sus ids de biblioteca y el
    calendario inyecta el feed completo — filtrar rompería el auto-poblado."""
    cine, tmdb, kitsu, anise = (_base('Cinemeta'), _base('TMDB'),
                                _base('Kitsu'), _base('AniSeason'))
    hoy = time.strftime('%Y-%m-%d')
    seen: set = set()
    out: list = []
    # ids semilla: se regenera cada petición — temporada nueva = ids nuevos
    top_ids, fs, fs_amv, td = _cal_seed_ids(cine, kitsu, anise)
    _cal_series_eps(cine, top_ids, seen, out)
    _cal_films(tmdb, fs, fs_amv, td, hoy, seen, out)
    days = _cfgget('calendar_months_ahead', 2) * 31        # _cfgget: override de perfil
    _cal_anime_eps(cine, days, seen, out)
    _cal_trakt(cine, days, hoy, seen, out)
    # orden: próximo estreno primero (vista navegable + calendario)
    out.sort(key=lambda m: min(((v.get('released') or '9')[:10] for v in
                                m.get('videos') or []
                                if (v.get('released') or '')[:10] > hoy),
                               default='9'))
    # SOLO metasDetailed: con 'metas' presente el cliente parsea la variante
    # ligera (sin videos) y el calendario no tiene nada que pintar
    return {'metasDetailed': out} if out else None
