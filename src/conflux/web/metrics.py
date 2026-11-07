"""/metrics — exposición Prometheus (text format 0.0.4) + observabilidad.

Series: salud por fuente (ok/fail/cooldown), streams servidos por fuente y
resolución, latencia por ruta (avg/p50/p95/p99/max), fails de disyuntor por
host y tamaños de caché. Todo gauge/counter — Prometheus scrapea cuando quiere.

OBS-008/009: `_diag_json()` — un snapshot JSON estructurado de TODO el estado
de observabilidad para soporte (versión, uptime, fuentes, latencias, caches,
histórico). Servido en /admin/diag con el mismo gate que /admin. Whitelist
de campos — nunca valores de config (secretos) ni tokens (QUAL-014).
El histórico diario (OBS-002) vive en `conflux.app.obs` — web puede leer
de app, app no puede leer de web (contrato de capas).
"""
import time

from conflux.core.state import (
    CLOCK,
    COOLDOWN,
    HEALTH,
    SOURCES,
    _SERVED,
    _cache,
    _cat_c,
    _fails,
    _meta_c,
    _rtime,
    _rts,
    manifests,
)
from conflux.app import obs as _obs

_T0 = CLOCK.time()                                   # uptime base

_H2 = '# HELP {n} {h}\n# TYPE {n} {t}\n'


def _esc(v):
    return str(v).replace('\\', '\\\\').replace('"', '\\"')


def _pct(vals, q):
    """Percentil q sobre una serie (mismo criterio que _admin_page)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def _metrics_text():
    now = CLOCK.time()
    out = [_H2.format(n='conflux_source_requests_total',
                      h='Respuestas upstream ok/fail por fuente', t='counter')]
    for n, h in HEALTH.items():
        out.append(f'conflux_source_requests_total{{source="{_esc(n)}",result="ok"}} {h.get("ok", 0)}')
        out.append(f'conflux_source_requests_total{{source="{_esc(n)}",result="fail"}} {h.get("fail", 0)}')
    out.append(_H2.format(n='conflux_source_cooldown_seconds',
                          h='Segundos de disyuntor restantes por fuente', t='gauge'))
    for n, cd in COOLDOWN.items():
        left = max(0.0, cd[0] - now)
        out.append(f'conflux_source_cooldown_seconds{{source="{_esc(n)}"}} {left:.0f}')
    out.append(_H2.format(n='conflux_streams_served_total',
                          h='Streams aportados por fuente y resolución', t='counter'))
    for n, sv in _SERVED.items():
        out.append(f'conflux_streams_served_total{{source="{_esc(n)}"}} {sv["n"]}')
        for r, c in sv.get('res', {}).items():
            out.append(f'conflux_streams_byres_total{{source="{_esc(n)}",res="{_esc(r)}"}} {c}')
    out.append(_H2.format(n='conflux_source_up',
                          h='Fuentes con manifest cargado (1) vs total', t='gauge'))
    out.append(f'conflux_source_up {sum(1 for m in manifests if m)}')
    out.append(f'conflux_sources_total {len(SOURCES)}')
    out.append(_H2.format(n='conflux_route_latency_ms',
                          h='Latencia por ruta (ms): avg/p50/p95/p99/max', t='gauge'))
    for k, (n2, t, x) in _rtime.items():
        avg = t / n2 if n2 else 0
        out.append(f'conflux_route_latency_ms{{route="{_esc(k)}",quantile="avg"}} {avg:.1f}')
        vals = list(_rts.get(k, ()))
        out.append(f'conflux_route_latency_ms{{route="{_esc(k)}",quantile="0.5"}} {_pct(vals, .5):.1f}')
        out.append(f'conflux_route_latency_ms{{route="{_esc(k)}",quantile="0.95"}} {_pct(vals, .95):.1f}')
        out.append(f'conflux_route_latency_ms{{route="{_esc(k)}",quantile="0.99"}} {_pct(vals, .99):.1f}')
        out.append(f'conflux_route_latency_ms{{route="{_esc(k)}",quantile="max"}} {x:.1f}')
    out.append(_H2.format(n='conflux_host_fail_streak',
                          h='Fallos consecutivos por host (disyuntor)', t='gauge'))
    for k, v in _fails.items():
        out.append(f'conflux_host_fail_streak{{host="{_esc(k)}"}} {v[0]}')
    out.append(_H2.format(n='conflux_cache_entries',
                          h='Entradas por caché', t='gauge'))
    out.append(f'conflux_cache_entries{{cache="stream"}} {len(_cache)}')
    out.append(f'conflux_cache_entries{{cache="meta"}} {len(_meta_c)}')
    out.append(f'conflux_cache_entries{{cache="catalog"}} {len(_cat_c)}')
    try:                                                     # EPG-002: matching canal↔programa
        from conflux.infra.epg import epg_stats
        es = epg_stats()
        if es:
            out.append(_H2.format(n='conflux_epg_match',
                                  h='Matching canal m3u <-> programa EPG', t='gauge'))
            for k in ('channels', 'mapped', 'with_programmes', 'programmes'):
                out.append(f'conflux_epg_match{{kind="{k}"}} {es.get(k, 0)}')
    except Exception:                                        # noqa: BLE001 — epg opcional
        pass
    try:                                                     # META-007: salud pósters + rpdb
        from conflux.domain.metafix import poster_stats
        out.append(_H2.format(n='conflux_poster',
                              h='Salud de posters y swaps RPDB', t='counter'))
        for k, v in poster_stats().items():
            out.append(f'conflux_poster{{kind="{k}"}} {v}')
    except Exception:                                        # noqa: BLE001 — metafix opcional
        pass
    return '\n'.join(x for x in out if x) + '\n'


def _diag_json():
    """Snapshot JSON completo del estado observable (OBS-008/009) para
    /admin/diag: versión+uptime, salud/cooldowns por fuente, streams por
    fuente/res, latencia por ruta, disyuntores, caches, pósters, epg e
    histórico diario (30 días). Whitelist explícita — NUNCA valores de
    config ni tokens (QUAL-014): el JSON se puede pegar en un reporte."""
    from conflux.web.server import _version           # local: evita ciclo import
    now = CLOCK.time()
    sources = {}
    for n in sorted(set(HEALTH) | set(COOLDOWN) | set(_SERVED)):
        h = HEALTH.get(n, {})
        cd = COOLDOWN.get(n)
        sv = _SERVED.get(n, {})
        sources[n] = {
            'ok': h.get('ok', 0), 'fail': h.get('fail', 0),
            'cooldown_left_s': round(max(0.0, cd[0] - now)) if cd else 0,
            'streams': sv.get('n', 0), 'streams_by_res': sv.get('res', {}),
            'last_err': h.get('err', ''), 'streak': h.get('streak', 0)}
    latency = {}
    for k, (n2, t, x) in _rtime.items():
        vals = list(_rts.get(k, ()))
        latency[k] = {'n': n2, 'avg_ms': round(t / n2, 1) if n2 else 0,
                      'p50_ms': round(_pct(vals, .5), 1),
                      'p95_ms': round(_pct(vals, .95), 1),
                      'p99_ms': round(_pct(vals, .99), 1),
                      'max_ms': round(x, 1)}
    try:
        from conflux.domain.metafix import poster_stats
        posters = poster_stats()
    except Exception:                                # noqa: BLE001
        posters = {}
    try:
        from conflux.infra.epg import epg_stats
        epg = epg_stats() or {}
    except Exception:                                # noqa: BLE001
        epg = {}
    days = sorted(_obs._HIST)[-30:]
    return {'version': _version(),
            'uptime_s': round(now - _T0),
            'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
            'sources_up': sum(1 for m in manifests if m),
            'sources_total': len(SOURCES),
            'sources': sources,
            'route_latency': latency,
            'host_fail_streaks': {k: v[0] for k, v in _fails.items()},
            'caches': {'stream': len(_cache), 'meta': len(_meta_c),
                       'catalog': len(_cat_c)},
            'posters': posters, 'epg': epg,
            'history_30d': {d: _obs._HIST[d] for d in days}}
