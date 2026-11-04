"""Indexers Newznab (NZBHydra2, Easynews vía hydra) como fuente Usenet.

Los items son NZB — Stremio no reproduce NZB directo: el stream apunta a
`/nzb/<b64>` que resolvemos por TorBox (el único debrid integrado que sirve
usenet). Por eso la fuente solo se activa con debrid_provider='torbox' —
emitir NZBs sin vía de reproducción sería ruido muerto en la lista."""
# pylint: disable=broad-exception-caught
import base64
import urllib.parse

from conflux.core.state import CFG, PORT, _health
from conflux.infra.net import _session

try:
    from defusedxml import ElementTree as _ET        # XML del indexer (no confiable)
except ImportError:
    _ET = None

_NZ_ATTR = '{http://www.newznab.org/DTD/2010/feeds/attributes/}'


def _num(v):
    """newznab:attr numérico tolerante ('', 'abc', '12,5' -> 0)."""
    try:
        return int(float(str(v).replace(',', '.').strip() or 0))
    except (TypeError, ValueError):
        return 0


def _nz_gate(stype, sid):
    """(url, params) si la fuente aplica, o None — off sin indexer/debrid."""
    url, key = CFG.get('newznab_url', '').rstrip('/'), CFG.get('newznab_key', '')
    imdb = sid.split(':')[0]
    if (not url or not key or _ET is None or not imdb.startswith('tt')
            or CFG.get('debrid_provider') != 'torbox' or not CFG.get('debrid_key')):
        return None
    params = {'t': 'movie' if stype == 'movie' else 'tvsearch',
              'imdbid': imdb, 'apikey': key, 'extended': '1'}
    parts = sid.split(':')
    if stype != 'movie' and len(parts) >= 3:          # tt:s:e -> season+ep
        params.update(season=parts[1], ep=parts[2])
    return url, params


def _nz_item(it):
    """item RSS -> stream /nzb/<b64> o None si no trae enclosure http."""
    attrs = {a.get('name'): a.get('value') for a in it.iter(_NZ_ATTR + 'attr')}
    enc = it.find('enclosure')                # enclosure@url = descarga NZB;
    link = enc.get('url', '') if enc is not None else ''   # <link> es detail-page, no el NZB
    if not link.startswith('http'):
        return None
    b64 = base64.urlsafe_b64encode(link.encode()).decode().rstrip('=')
    s: dict = {'name': 'Usenet', 'title': it.findtext('title') or 'usenet',
               'url': f'http://127.0.0.1:{PORT}/nzb/{b64}',
               'behaviorHints': {'bingeGroup': 'usenet'}}
    sz = _num(attrs.get('size') or (enc.get('length') if enc is not None else 0))
    if sz:
        s['behaviorHints']['videoSize'] = sz
    return s


def _newznab(stype, sid):
    """GET api?t=(movie|tvsearch)&imdbid -> streams Usenet vía /nzb/."""
    gate = _nz_gate(stype, sid)
    if gate is None:
        return []
    url, params = gate
    try:
        r = _session(urllib.parse.urlsplit(url).netloc).get(
            url, params=params, timeout=10)
        out = [x for x in (_nz_item(it) for it in
                           _ET.fromstring(r.content).iter('item')) if x]
        _health('Newznab', True, '', len(out))
        return out[:30]
    except Exception:                                 # noqa: BLE001 — indexer nunca tumba _collect
        _health('Newznab', False, 'error', 0)
        return []
