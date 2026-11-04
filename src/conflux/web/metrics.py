"""/metrics — exposición Prometheus (text format 0.0.4).

Series: salud por fuente (ok/fail/cooldown), streams servidos por fuente y
resolución, latencia por ruta (avg/p50/p95/p99/max), fails de disyuntor por
host y tamaños de caché. Todo gauge/counter — Prometheus scrapea cuando quiere.
"""
from conflux.core.state import (
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
import time

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
    now = time.time()
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
    return '\n'.join(x for x in out if x) + '\n'
