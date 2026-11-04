"""Easynews Global Search 2.0 como fuente Usenet directa (sin TorBox).

Los items de `members.easynews.com/2.0/search/solr-search/` llevan su propio
enlace de descarga (basic-auth) — Stremio lo reproduce directo: `/en/<b64>`
re-inyecta `user:pass@` en el redirect para no filtrar credenciales al manifest.
La query necesita TÍTULO (Easynews no entiende imdb) — se toma del meta que el
cliente ya cargó al abrir la ficha (`_meta_c`); si aún no está, no hay query."""
# pylint: disable=broad-exception-caught
import base64
import re
import urllib.parse

from conflux.core.state import CFG, PORT, _health, _meta_title
from conflux.infra.net import _session


def _en_title(stype, sid):
    """(query, show_se) — título del meta cacheado + sXXeYY si es episodio."""
    _, t, se = _meta_title(stype, sid)
    return t, se


def _en_bad(it):
    """Item no emitible: sin hash/filename, con passwd/virus, no-vídeo o clip."""
    if not (it.get('hash') or it.get('0')) or not (it.get('filename') or it.get('10')):
        return True
    if it.get('passwd') or it.get('virus'):
        return True
    if it.get('type') and str(it['type']).upper() != 'VIDEO':
        return True
    dur = str(it.get('duration') or it.get('14') or '')
    return bool(re.match(r'^(\d+s|[0-5]m)', dur))      # clips <5min fuera


def _en_item(it, base_url):
    """Item easynews (dict nuevo o array legacy de claves numéricas) -> stream."""
    if _en_bad(it):
        return None
    h, fn = it.get('hash') or it.get('0'), it.get('filename') or it.get('10')
    ext = it.get('ext') or it.get('11') or ''
    link = f'{base_url}/{urllib.parse.quote(f"{h}{ext}/{fn}{ext}")}'
    b64 = base64.urlsafe_b64encode(link.encode()).decode().rstrip('=')
    s: dict = {'name': 'Easynews', 'title': f'{fn}{ext}',
         'url': f'http://127.0.0.1:{PORT}/en/{b64}',
         'behaviorHints': {'bingeGroup': 'easynews'}}
    try:
        sz = it.get('size') or it.get('4')
        if sz:
            s['behaviorHints']['videoSize'] = int(float(sz))
    except (TypeError, ValueError):
        pass
    return s


def _easynews(stype, sid):
    """solr-search por título -> streams /en/. Off sin credenciales/título."""
    user, pw = CFG.get('easynews_user', ''), CFG.get('easynews_pass', '')
    if not user or not pw or stype not in ('movie', 'series', 'anime'):
        return []
    if not sid.split(':')[0].startswith('tt'):
        return []
    title, se = _en_title(stype, sid)
    if not title:
        return []                                     # meta aún no cacheado
    try:
        return _en_search(user, pw, f'{title} {se}' if se else title)
    except Exception:                                 # noqa: BLE001 — fuente nunca tumba _collect
        _health('Easynews', False, 'error', 0)
        return []


def _en_search(user, pw, q):
    """GET solr-search -> hasta 40 streams /en/ ordenados por relevancia."""
    s = _session('members.easynews.com')
    r = s.get('https://members.easynews.com/2.0/search/solr-search/',
              params={'fly': '2', 'sb': '1', 'pno': '1', 'pby': '60',
                      'u': '1', 'chxu': '1', 'chxgx': '1', 'st': 'basic',
                      'gps': q, 'vv': '1', 'safeO': '0',
                      's1': 'relevance', 's1d': '-', 'fty[]': 'VIDEO'},
              auth=(user, pw), timeout=10)
    d = r.json()
    base_url = '/'.join(p for p in (str(d.get(k, '')).strip('/') for k in
                                    ('downURL', 'dlFarm', 'dlPort')) if p) \
        or 'https://members.easynews.com/dl'
    out = [x for x in (_en_item(it, base_url) for it in d.get('data', [])) if x]
    _health('Easynews', True, '', len(out))
    return out[:40]
