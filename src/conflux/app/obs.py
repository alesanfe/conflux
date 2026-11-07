"""Histórico de observabilidad (OBS-002) — rollup diario persistido.

`_hist_snapshot()` acumula un rollup DIARIO de los contadores globales
(req ok/fail upstream, streams servidos, pósters muertos, EPG con programa)
en `.metrics-history.json` — escritura atómica, anillo de 90 días, deltas
contra el último snapshot (un crash pierde <1 día). Se invoca desde el
refresher y en el shutdown. `_HIST` lo lee `web.metrics` para el
`history_30d` de /admin/diag (capa app: web puede importarla, al revés no).
"""
import json
import os
import time

from conflux.core.state import (
    CLOCK,
    HEALTH,
    _SERVED,
    _atomic_json,
    _ROOT,
)

_HIST_PATH = os.path.join(_ROOT, '.metrics-history.json')
_HIST: dict = {}                                     # 'YYYY-MM-DD' -> contadores del día
_HIST_KEEP = 90
_PREV: dict = {}                                     # snapshot de contadores absolutos


def _counters():
    """Contadores globales absolutos (los que se rollup-an por día)."""
    c = {'req_ok': 0, 'req_fail': 0, 'streams': 0}
    for h in HEALTH.values():
        c['req_ok'] += h.get('ok', 0)
        c['req_fail'] += h.get('fail', 0)
    for sv in _SERVED.values():
        c['streams'] += sv.get('n', 0)
    try:
        from conflux.domain.metafix import poster_stats
        ps = poster_stats()
        c['poster_dead'] = ps.get('poster_dead', 0)
        c['poster_replaced'] = ps.get('poster_replaced', 0)
    except Exception:                                # noqa: BLE001 — metafix opcional
        pass
    try:
        from conflux.infra.epg import epg_stats
        es = epg_stats() or {}
        c['epg_programmes'] = es.get('programmes', 0)
        c['epg_mapped'] = es.get('mapped', 0)
    except Exception:                                # noqa: BLE001 — epg opcional
        pass
    return c


def _hist_load():
    """'.metrics-history.json' -> _HIST + _PREV. Tolerante: archivo roto -> vacío."""
    try:
        with open(_HIST_PATH, encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    days = raw.get('days')
    if isinstance(days, dict):
        _HIST.update({str(k): v for k, v in days.items() if isinstance(v, dict)})
        while len(_HIST) > _HIST_KEEP:               # anillo: fuera el más viejo
            _HIST.pop(min(_HIST))
    prev = raw.get('prev')
    if isinstance(prev, dict):
        _PREV.update({str(k): v for k, v in prev.items() if isinstance(v, (int, float))})


def _hist_snapshot():
    """Rollup del día: delta de contadores desde el último snapshot -> _HIST,
    prune a 90 días y persistencia atómica (OBS-002)."""
    cur = _counters()
    day = time.strftime('%Y-%m-%d', time.gmtime(CLOCK.time()))
    ent = _HIST.setdefault(day, {})
    for k, v in cur.items():
        p = _PREV.get(k, 0)
        # v < p: contador reiniciado (restart del proceso — _PREV persiste en
        # disco): TODO v es nuevo, sin este guard el tramo se perdía entero
        d = v - p if v >= p else v
        if d > 0:
            ent[k] = ent.get(k, 0) + d
    _PREV.clear()
    _PREV.update(cur)
    while len(_HIST) > _HIST_KEEP:
        _HIST.pop(min(_HIST))
    try:
        _atomic_json(_HIST_PATH, {'days': _HIST, 'prev': _PREV})
    except OSError:
        pass                                         # disco lleno: no tumba


_hist_load()
