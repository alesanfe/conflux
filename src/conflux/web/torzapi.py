"""Torznab/Newznab API — conflux como indexer para *arr (Sonarr/Radarr/Prowlarr).

GET /torznab/api (o /newznab/api) con `t=caps|movie|tvsearch` devuelve el
feed RSS Newznab estándar: los streams agregados (infoHash -> magnet,
nzbUrl -> enclosure NZB) salen como items que un *arr puede mandar a su
download client. Auth: `apikey` contra `torzapi_key` (o `admin_token` si
aquella está vacía); sin ninguna configurada queda abierto (loopback).
"""
import base64
import email.utils
import hmac
import re
import time
import urllib.parse
from xml.sax.saxutils import escape

from conflux.app.stream import _ensure_collect
from conflux.core.cfg import _eff_disabled_for
from conflux.core.state import CFG, STREAM_BUDGET, log_ev

_CAP = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<caps><server version="0.1" title="conflux"/>'
        '<limits max="200" default="100"/>'
        '<searching>'
        '<search available="yes" supportedParams="q,imdbid"/>'
        '<tv-search available="yes" supportedParams="q,imdbid,season,ep"/>'
        '<movie-search available="yes" supportedParams="q,imdbid"/>'
        '</searching><categories>'
        '<category id="2000" name="Movies"/><category id="5000" name="TV"/>'
        '</categories></caps>')

_TZ_ATTR = '{http://www.newznab.org/DTD/2010/feeds/attributes/}'


def _torz_auth(q):
    """apikey valida (compare_digest) o abierto si no hay clave configurada."""
    want = CFG.get('torzapi_key') or CFG.get('admin_token') or ''
    if not want:
        return True
    got = (q.get('apikey') or [''])[0]
    return hmac.compare_digest(str(want), str(got))


def _err(code, txt):
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<error code="{code}" description="{escape(txt)}"/>')


def _rss(items, cat):
    its = ''.join(items)
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<rss version="2.0" xmlns:newznab="http://www.newznab.org/DTD/2010/feeds/attributes/">'
            f'<channel><title>conflux</title>{its}</channel></rss>')


def _nzb_link(out):
    """Stream usenet interno (/nzb/<b64>) -> URL NZB real; nzbUrl directo también vale."""
    nzb = out.get('nzbUrl')
    if isinstance(nzb, str) and nzb.startswith('http'):
        return nzb
    u = out.get('url') or ''
    if '/nzb/' not in u:
        return ''
    b = u.rsplit('/nzb/', 1)[-1].split('?')[0]
    try:
        link = base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return ''
    return link if link.startswith('http') else ''


def _row_items(r, cat):
    """Fila del collect (norm_stream) -> <item> RSS Newznab (torrent y/o usenet)."""
    try:
        out, _res, seeds, size, _cached, f, _short = r
    except (TypeError, ValueError):
        return ''
    title = (f.get('title') if isinstance(f, dict) else None) or out.get('title') or 'release'
    title = str(title).strip()[:300] or 'release'
    ih, entries = out.get('infoHash'), []
    if isinstance(ih, str) and ih:
        mag = (f"magnet:?xt=urn:btih:{ih}&dn={urllib.parse.quote(title)}"
               f'&tr=udp://tracker.opentrackr.org:1337/announce'
               f'&tr=udp://open.stealth.si:80/announce')
        entries.append((mag, 'application/x-bittorrent', seeds, ih))
    link = _nzb_link(out)
    if link:
        entries.append((link, 'application/x-nzb', 0, ''))
    when = email.utils.formatdate(time.time())
    size_b = int(size * (1 << 30))       # filas llevan GB (GiB); newznab size/length = bytes
    outx = []
    for u, mime, sd, h in entries:
        attrs = (f'<newznab:attr name="category" value="{cat}"/>'
                 f'<newznab:attr name="size" value="{size_b}"/>'
                 f'<newznab:attr name="seeders" value="{int(sd)}"/>'
                 f'<newznab:attr name="magneturl" value="{escape(u)}"/>')
        if h:
            attrs += f'<newznab:attr name="infohash" value="{escape(h.lower())}"/>'
        outx.append(
            f'<item><title>{escape(title)}</title>'
            f'<guid isPermaLink="false">{escape(u[:120])}</guid>'
            f'<link>{escape(u)}</link>'
            f'<pubDate>{when}</pubDate>'
            f'<enclosure url="{escape(u)}" length="{size_b}" type="{mime}"/>'
            f'{attrs}</item>')
    return ''.join(outx)


def handle_torzapi(q):
    """GET /torznab/api -> (status, xml). q = parse_qs del query."""
    if not _torz_auth(q):
        return 401, _err(100, 'Invalid API Key')
    t = (q.get('t') or ['caps'])[0].lower()
    if t in ('caps', 'capabilities'):
        return 200, _CAP
    stype, cat = ('movie', 2000) if t in ('movie', 'moviesearch') else \
                 ('series', 5000) if t in ('tvsearch', 'tv') else (None, 2000)
    imdb = (q.get('imdbid') or [''])[0].lower()
    if stype is None or not re.fullmatch(r'tt\d{5,10}', imdb):
        # búsquedas de texto (q=) sin imdb: nuestro collect es por id — feed vacío
        return 200, _rss('', cat)
    sid = imdb
    if stype == 'series':
        se, ep = (q.get('season') or [''])[0], (q.get('ep') or [''])[0]
        if se.lstrip('0').isdigit() and ep.lstrip('0').isdigit():
            sid = f'{imdb}:{int(se)}:{int(ep)}'
    key = (stype, sid, tuple(sorted(_eff_disabled_for(stype))))
    state = _ensure_collect(key)
    state['done'].wait(STREAM_BUDGET)
    with state['lock']:
        rows = sorted(state['rows'], key=lambda r: -(r[2] if len(r) > 2 else 0))[:200]
    log_ev('torzapi', t=t, sid=sid, n=len(rows))
    return 200, _rss((_row_items(r, cat) for r in rows), cat)
