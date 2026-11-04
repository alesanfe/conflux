"""Indexers Newznab (NZBHydra2, Easynews vía hydra) como fuente Usenet.

Los items son NZB — Stremio no reproduce NZB directo, hay dos vías:
- `/nzb/<b64>` resuelto por TorBox (el único debrid integrado que sirve
  usenet — con multi-debrid basta que torbox sea UNO de los providers).
- NNTP nativo (`nntp_host`+`nntp_user`+`nntp_pass`): el stream lleva
  `nzbUrl`+`servers` — el propio cliente Stremio (Desktop v5 / Android TV)
  conecta al servidor NNTP y reproduce sin backend (estilo "Stremio NNTP"
  de AIOStreams). Sin ninguna vía configurada la fuente no emite."""
# pylint: disable=broad-exception-caught
import base64
import urllib.parse

from conflux.core.state import CFG, PORT, _health
from conflux.infra.debrid import _db_key
from conflux.infra.net import _session

try:
    from defusedxml import ElementTree as _ET        # XML del indexer (no confiable)
except ImportError:
    _ET = None

_NZ_ATTR = '{http://www.newznab.org/DTD/2010/feeds/attributes/}'

_NZ_VIDEO = '/.mkv$|.mp4$|.avi$|.m4v$|.webm$|.ts$/i'


def _num(v):
    """newznab:attr numérico tolerante ('', 'abc', '12,5' -> 0)."""
    try:
        return int(float(str(v).replace(',', '.').strip() or 0))
    except (TypeError, ValueError):
        return 0


def _nntp_server():
    """URL `nntp(s)://user:pass@host:port/conns` del SDK, o None sin config."""
    host, user = CFG.get('nntp_host', ''), CFG.get('nntp_user', '')
    pw = CFG.get('nntp_pass', '')
    if not (host and user and pw):
        return None
    scheme = 'nntps' if CFG.get('nntp_ssl', True) else 'nntp'
    port = CFG.get('nntp_port', 563)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 563
    conns = CFG.get('nntp_connections', 5)
    try:
        conns = max(1, int(conns))
    except (TypeError, ValueError):
        conns = 5
    cred = f'{urllib.parse.quote(user, safe="")}:{urllib.parse.quote(pw, safe="")}@'
    return f'{scheme}://{cred}{host}:{port}/{conns}'


def _nz_gate(stype, sid):
    """(url, params) si la fuente aplica, o None — off sin indexer/vía usenet."""
    url, key = CFG.get('newznab_url', '').rstrip('/'), CFG.get('newznab_key', '')
    imdb = sid.split(':')[0]
    if (not url or not key or _ET is None or not imdb.startswith('tt')
            or not (_db_key('torbox') or _nntp_server())):
        return None
    params = {'t': 'movie' if stype == 'movie' else 'tvsearch',
              'imdbid': imdb, 'apikey': key, 'extended': '1'}
    parts = sid.split(':')
    if stype != 'movie' and len(parts) >= 3:          # tt:s:e -> season+ep
        params.update(season=parts[1], ep=parts[2])
    return url, params


def _nz_item(it):
    """item RSS -> streams usenet o None si no trae enclosure http.

    Emite la variante TorBox (/nzb/) y/o la nativa NNTP (nzbUrl+servers)
    según lo configurado — con ambas salen dos streams."""
    attrs = {a.get('name'): a.get('value') for a in it.iter(_NZ_ATTR + 'attr')}
    enc = it.find('enclosure')                # enclosure@url = descarga NZB;
    link = enc.get('url', '') if enc is not None else ''   # <link> es detail-page, no el NZB
    if not link.startswith('http'):
        return None
    title = it.findtext('title') or 'usenet'
    sz = _num(attrs.get('size') or (enc.get('length') if enc is not None else 0))
    out = []
    if _db_key('torbox'):
        b64 = base64.urlsafe_b64encode(link.encode()).decode().rstrip('=')
        s: dict = {'name': 'Usenet', 'title': title,
                   'url': f'http://127.0.0.1:{PORT}/nzb/{b64}',
                   'behaviorHints': {'bingeGroup': 'usenet'}}
        if sz:
            s['behaviorHints']['videoSize'] = sz
        out.append(s)
    srv = _nntp_server()
    if srv:
        s2: dict = {'name': 'Usenet SN', 'title': title,
                    'nzbUrl': link, 'servers': [srv],
                    'fileMustInclude': _NZ_VIDEO,
                    'behaviorHints': {'bingeGroup': 'usenet'}}
        if sz:
            s2['behaviorHints']['videoSize'] = sz
        out.append(s2)
    return out or None


def _newznab(stype, sid):
    """GET api?t=(movie|tvsearch)&imdbid -> streams Usenet (TorBox y/o NNTP)."""
    gate = _nz_gate(stype, sid)
    if gate is None:
        return []
    url, params = gate
    try:
        r = _session(urllib.parse.urlsplit(url).netloc).get(
            url, params=params, timeout=10)
        out = [x for it in _ET.fromstring(r.content).iter('item')
               for x in (_nz_item(it) or [])]
        _health('Newznab', True, '', len(out))
        return out[:30]
    except Exception:                                 # noqa: BLE001 — indexer nunca tumba _collect
        _health('Newznab', False, 'error', 0)
        return []
