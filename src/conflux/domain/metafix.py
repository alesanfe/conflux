"""Normalización y enriquecimiento de metas Stremio (poster/logo/awards/vídeos)."""
# pylint: disable=broad-exception-caught,line-too-long
import html as _html
import os as _os
import re as _re
import urllib.parse as _urlparse

import requests

from conflux.core.cfg import _cfgget
from conflux.core import state as _st
from conflux.core.state import CLOCK as _CLOCK, _POOL, _POOL_BG
from conflux.infra.net import _session
from conflux.infra.wikidata import _wikidata

# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido



_YT_AWARDS = [(100e6, 'Red Diamond'), (50e6, 'Ruby'), (10e6, 'Diamond'),
              (1e6, 'Gold'), (1e5, 'Silver')]

_RE_TAG = _re.compile(r'<[^>]+>')
_RE_BLOCK = _re.compile(r'<\s*(?:br|/p|/li|/div)\b[^>]*>', _re.IGNORECASE)


# ---- posters muertos (R2): metahub 404 en títulos sin cobertura — el cliente
# muestra su glyph gris de "sin imagen"; mejor un placeholder uniforme ----
_POSTER_BAD: dict = {}        # url -> (strikes, ts) — mismo contrato que _URL_BAD
_POSTER_OK: dict = {}         # url -> ts
_POSTER_PEND: set = set()     # urls con HEAD en vuelo (dedupe + cap de ráfaga)
_POSTER_FB = 'https://i.imgur.com/tTCuczU.png'   # el placeholder de los m3u
_POSTERH_PATH = _os.path.join(_st._ROOT, '.poster-health.json')   # persistidos
_MF_STATS = {'poster_dead': 0, 'poster_replaced': 0, 'poster_resurrect': 0,
             'rpdb_applied': 0}                          # META-007


def poster_stats():
    """Snapshot de contadores de salud de pósters/rpdb para /metrics."""
    return dict(_MF_STATS)


def _poster_head(u):
    """HEAD async: <400 -> ok; 4xx/5xx -> muerto; resto -> 1 strike."""
    try:
        r = _session(_urlparse.urlsplit(u).netloc).head(u, timeout=3, allow_redirects=True)
        if r.status_code < 400:
            _POSTER_OK[u] = _CLOCK.time()
            if _POSTER_BAD.pop(u, None):
                _MF_STATS['poster_resurrect'] += 1       # resucitó: era malo
        else:
            if _POSTER_BAD.get(u, (0, 0))[0] < 2:        # transición a muerto
                _MF_STATS['poster_dead'] += 1            # (re-confirmar no cuenta)
            _POSTER_BAD[u] = (2, _CLOCK.time())
    except Exception:                                    # noqa: BLE001
        st, _ = _POSTER_BAD.get(u, (0, 0))
        _POSTER_BAD[u] = (st + 1, _CLOCK.time())
        if st + 1 == 2:                                  # solo la transición
            _MF_STATS['poster_dead'] += 1                # segundo strike: muerto
    _st.health_save(_POSTERH_PATH, _POSTER_BAD, _POSTER_OK)   # META-003
    _POSTER_PEND.discard(u)


def _poster_probe(m):
    """Poster desconocido -> HEAD encolado (fire-and-forget, no muta el meta:
    la primera carga puede salir rota, las siguientes ven el veredicto)."""
    p = m.get('poster')
    if not isinstance(p, str) or not p.startswith('http'):
        return
    now = _CLOCK.time()
    if len(_POSTER_BAD) + len(_POSTER_OK) > 2000:        # misma cota que streams
        for u in sorted(_POSTER_BAD, key=lambda u: _POSTER_BAD[u][1])[:len(_POSTER_BAD) // 2]:
            _POSTER_BAD.pop(u, None)
        for u in sorted(_POSTER_OK, key=lambda u: _POSTER_OK[u])[:len(_POSTER_OK) // 2]:
            _POSTER_OK.pop(u, None)
    ts = _POSTER_BAD.get(p, (0, 0))[1]
    if (now - ts > 1800 and now - _POSTER_OK.get(p, 0) > 1800
            and len(_POSTER_PEND) < 32 and p not in _POSTER_PEND):
        _POSTER_PEND.add(p)
        try:
            _POOL_BG.submit(_poster_head, p)
        except Exception:                                # noqa: BLE001 — pool cerrado
            _POSTER_PEND.discard(p)


def _posters_view(d):
    """Vista de emisión: posters confirmados muertos -> placeholder sobre
    COPIA del meta — el objeto cacheado conserva la url original (si resucita
    a los 30min el caché no queda condenado). En cola también los desconocidos
    de respuestas cacheadas."""
    if not isinstance(d, dict):
        return d
    now = _CLOCK.time()
    dead = set()
    ms = (list(d.get('metas') or []) + list(d.get('metasDetailed') or [])
          + ([d['meta']] if isinstance(d.get('meta'), dict) else []))
    for m in ms:
        if not isinstance(m, dict):
            continue
        _poster_probe(m)
        p = m.get('poster')
        if not isinstance(p, str):
            continue
        v = _POSTER_BAD.get(p)
        if v is not None and v[0] >= 2 and now - v[1] < 1800:
            dead.add(p)
    if not dead:
        return d
    _MF_STATS['poster_replaced'] += len(dead)
    out = dict(d)
    for k in ('meta', 'metas', 'metasDetailed'):
        v = d.get(k)
        if isinstance(v, dict) and v.get('poster') in dead:
            out[k] = dict(v, poster=_POSTER_FB)
        elif isinstance(v, list):
            out[k] = [dict(m, poster=_POSTER_FB)
                      if isinstance(m, dict) and m.get('poster') in dead else m
                      for m in v]
    return out


def _fix_meta_html(m):
    """stremio-web renderiza description como TEXTO: el HTML de upstreams
    (AniList manda '<i>título</i><br>...') sale literal en la ficha.
    Bloques -> espacio, resto de tags fuera, entidades decodificadas."""
    d = m.get('description')
    if not isinstance(d, str):
        return
    if '<' in d:
        d = _html.unescape(_RE_TAG.sub('', _RE_BLOCK.sub(' ', d))).replace('\xa0', ' ')
    ls = [ln for ln in d.split('\n') if ln.strip()]
    if len(ls) > 1 and all(_re.match(r'^\S{1,15}:', ln) for ln in ls):
        d = ' · '.join(ls)    # 'Country: CR\nCategories: x' renderiza en una línea
    m['description'] = _re.sub(r'\s{2,}', ' ', d).strip()



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
    if fd:
        _fix_meta_vids(m, vids, fd.result())



_RPDB_STYLES = {'poster-default', 'poster-textless'}   # estilos del servicio


def _tt_id(m):
    """imdb id de un meta dict si empieza por tt (solo esos llevan RPDB)."""
    mid = str(m.get('id') or '') if isinstance(m, dict) else ''
    return mid if mid.startswith('tt') else ''


def _rpdb_url(mid):
    """URL RPDB para un imdb id: rpdb_style validado contra allowlist (la key
    va en el path — un style inyectado rompería la URL) y ?fallback=true si
    rpdb_fallback (default on): sin poster con rating, RPDB devuelve el
    original en vez de 404 -> la ficha nunca se queda sin imagen."""
    style = str(_cfgget('rpdb_style') or 'poster-default')
    if style not in _RPDB_STYLES:
        style = 'poster-default'
    fb = '?fallback=true' if _cfgget('rpdb_fallback', True) else ''
    return (f'https://api.ratingposterdb.com/{_cfgget("rpdb_key")}'
            f'/imdb/{style}/{mid}.jpg{fb}')


def _rpdb(d):
    """Posters con ratings (RatingPosterDB — la feature de MediaFusion): si hay
    rpdb_key configurada, los metas tt:* sirven su poster vía api.ratingposterdb.com.
    Sin key (default) no toca nada. NO destructivo: devuelve copia para no
    contaminar los caches compartidos entre perfiles (/p/, /cfg/).
    META-002: estilo+fallback configurables (_rpdb_url). META-007:
    rpdb_applied cuenta swaps servidos."""
    if not _cfgget('rpdb_key') or not isinstance(d, dict):
        return d
    out = dict(d)
    n = 0
    for fld in ('metas', 'metasDetailed'):
        ms = out.get(fld)
        if isinstance(ms, list):
            out[fld] = [dict(m, poster=_rpdb_url(m['id'])) if _tt_id(m) else m
                        for m in ms]
            n += sum(1 for m in ms if _tt_id(m))
    m = out.get('meta')
    if isinstance(m, dict) and _tt_id(m):
        out['meta'] = dict(m, poster=_rpdb_url(m['id']))
        n += 1
    _MF_STATS['rpdb_applied'] += n
    return out



def _fix_metas(d, deep=False):
    """Canal yt: avatar ggpht caducado -> unavatar; descripción vacía -> stats reales;
    deep=True (detalle): + Wikidata (país, web, social, año), awards por subs, logo, vídeos muertos."""
    if not isinstance(d, dict):
        return d
    ms = (list(d.get('metas') or []) + list(d.get('metasDetailed') or [])
          + ([d['meta']] if isinstance(d.get('meta'), dict) else []))
    for m in ms:
        if not isinstance(m, dict):
            continue
        _fix_meta_html(m)
        _poster_probe(m)
        yt = m.get('yt_id')
        i = str(m.get('id') or '')
        if not yt and i.startswith('yt_id:'):
            yt = i[6:]
        if not yt:
            continue
        _fix_one_meta(m, yt, deep)
    return d


def _fix_one_meta(m, yt, deep):
    """Un meta yt: basic siempre; deep solo en detalle."""
    _fix_meta_basic(m, yt)
    if deep:
        _fix_meta_deep(m, yt)



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


_st.health_load(_POSTERH_PATH, _POSTER_BAD, _POSTER_OK)   # veredictos del proceso anterior
