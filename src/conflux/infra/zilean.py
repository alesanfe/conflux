"""Zilean local (self-hosted, como Prowlarr): hashlists DMM -> streams.

Zilean indexa los dumps de DebridMediaManager — hashes ya cacheados en los
debrid públicos. `GET {zilean_url}/dmm/filtered?ImdbId=tt...&Season=&Episode=`
devuelve [{filename, infoHash, filesize}] — se emiten como streams con
behaviorHints.cached (debrid los marca ⚡ al verificarlos)."""
# pylint: disable=broad-exception-caught
import urllib.parse

from conflux.core.state import CFG
from conflux.infra.net import _session


def _num(v):
    """Entero tolerante: str/float -> int; basura -> None."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _zilean(stype, sid):
    """Consulta Zilean por IMDb id -> [streams]. Vacío si no hay url o no es tt."""
    base = (CFG.get('zilean_url') or '').rstrip('/')
    if not base or stype not in ('movie', 'series', 'anime') or not sid.startswith('tt'):
        return []
    parts = sid.split(':')
    q = {'ImdbId': parts[0]}
    if len(parts) >= 3:                                  # tt:s:e -> season+episode
        q['Season'], q['Episode'] = parts[1], parts[2]
    try:
        d = _session(urllib.parse.urlsplit(base).hostname or 'zilean').get(
            f'{base}/dmm/filtered', params=q, timeout=10).json()
    except Exception:                                    # noqa: BLE001 — indexer caído -> 0
        return []
    items = d if isinstance(d, list) else (d.get('results') or d.get('data') or []) \
        if isinstance(d, (list, dict)) else []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        h = (it.get('infoHash') or it.get('info_hash') or it.get('hash') or '')
        name = (it.get('filename') or it.get('raw_title') or it.get('title') or '')
        if not h or not name:
            continue
        s = {'name': 'Zilean', 'title': str(name),
             'infoHash': str(h).lower(),
             'behaviorHints': {'cached': True}}          # DMM = ya en caché debrid
        sz = _num(it.get('filesize') or it.get('size') or it.get('bytes'))
        if sz:
            s['behaviorHints']['videoSize'] = sz
        out.append(s)
    return out[:40]                                      # hashlists pueden ser enormes
