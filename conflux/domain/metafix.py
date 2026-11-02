"""Normalización y enriquecimiento de metas Stremio (poster/logo/awards/vídeos)."""
# pylint: disable=broad-exception-caught,line-too-long
import requests

from conflux.core.state import _POOL
from conflux.infra.net import _session
from conflux.infra.wikidata import _wikidata

# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido



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
