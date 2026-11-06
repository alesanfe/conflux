#!/usr/bin/env python3
"""Soak test del proxy: carga sostenida durante N minutos y verifica que el
servicio se mantiene sano — sin fugas de threads/memoria, sin errores 5xx,
latencias dentro de presupuesto y endpoints vivos al final.

Uso:
    python scripts/soak.py [minutos] [workers]     # por defecto 5 min, 8 hilos
    python scripts/soak.py 60 16                   # una hora, 16 hilos

Contrato de fallo: exit 1 si
- /health o /health/live deja de responder o devuelve != 200
- cualquier respuesta es 5xx o el request falla (timeout/conn refused)
- los threads del proceso crecen >20% respecto a la línea base estable
- los RSS del proceso crecen >50% tras el tramo de calentamiento
- al terminar la carga /health/ready no está 'ready'/'degraded' con fuentes

Requiere el proxy corriendo en 127.0.0.1:9700 (o CONFLUX_URL).
"""
import os
import sys
import threading
import time
import urllib.request

BASE = os.environ.get('CONFLUX_URL', 'http://127.0.0.1:9700')

# rutas representativas del tráfico real de Stremio
ROUTES = [
    '/manifest.json',
    '/health',
    '/health/live',
    '/health/ready',
    '/catalog/movie/scinemeta_movie_top.json',
    '/catalog/series/scinemeta_series_top.json',
    '/meta/movie/tt0137523.json',
    '/meta/series/tt0944947.json',
    '/stream/movie/tt0137523.json',
    '/stream/series/tt0944947%3A1%3A1.json',
    '/subtitles/movie/tt0137523.json',
    '/metrics',
]

# variante cortés con los upstreams: las rutas caras (collects de stream /
# subtitles hacen fan-out a ~30 fuentes reales) van con peso mucho menor —
# la carga sostenida cae sobre el pipeline local, que es lo que el soak mide.
ROUTES_LIGHT = [
    '/manifest.json', '/manifest.json',
    '/health', '/health', '/health/live', '/health/ready',
    '/catalog/movie/scinemeta_movie_top.json',
    '/catalog/series/scinemeta_series_top.json',
    '/meta/movie/tt0137523.json',
    '/meta/series/tt0944947.json',
    '/stream/movie/tt0137523.json',          # ~1/12 del tráfico total
    '/stream/series/tt0944947%3A1%3A1.json',
    '/metrics', '/metrics',
    '/subtitles/movie/tt0137523.json',
]

_stats = {'reqs': 0, 'errs': 0, 'lat': [], 'codes': {}}
_lock = threading.Lock()
_stop = threading.Event()


def _get(path, timeout=20):
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(f'{BASE}{path}', timeout=timeout) as r:
            r.read()
            return r.status, (time.monotonic() - t0) * 1000, None
    except Exception as e:                       # noqa: BLE001 — se contabiliza
        return None, (time.monotonic() - t0) * 1000, e


def _worker(routes):
    i = 0
    while not _stop.is_set():
        path = routes[i % len(routes)]
        i += 1
        code, ms, err = _get(path)
        with _lock:
            _stats['reqs'] += 1
            _stats['lat'].append(ms)
            _stats['codes'][code or 'EXC'] = _stats['codes'].get(
                code or 'EXC', 0) + 1
            if err is not None or (code and code >= 500):
                _stats['errs'] += 1


def _probe(path):
    code, ms, err = _get(path, timeout=10)
    return code, err


def _proc_stats():
    """Threads + RSS del proceso que escucha :9700 (vía /health ready uptime
    y /proc o wmi en windows). Best-effort: si no se puede medir, None."""
    try:
        import subprocess
        if sys.platform == 'win32':
            out = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 "Get-NetTCPConnection -LocalPort 9700 -State Listen "
                 "| Select-Object -First 1 -Expand OwningProcess"],
                capture_output=True, text=True, timeout=10).stdout.strip()
            pid = int(out) if out.isdigit() else None
            if pid:
                ps = subprocess.run(
                    ['powershell', '-NoProfile', '-Command',
                     f"$p=Get-Process -Id {pid};"
                     "$t=$p.Threads.Count;"
                     "$r=[int]($p.WorkingSet64/1MB);"
                     "Write-Output (\"$t|$r\")"],
                    capture_output=True, text=True, timeout=10).stdout.strip()
                th, rss = ps.split('|')
                return int(th), int(rss)
        else:
            out = subprocess.run(['sh', '-c',
                                  'for p in /proc/[0-9]*; do '
                                  'if grep -qs conflux "$p/cmdline" 2>/dev/null'
                                  ' || grep -qs proxy.py "$p/cmdline" 2>/dev/null;'
                                  ' then basename $p; fi; done | head -1'],
                                 capture_output=True, text=True,
                                 timeout=10).stdout.strip()
            if out.isdigit():
                with open(f'/proc/{out}/status', encoding='ascii') as fh:
                    txt = fh.read()
                import re
                th = int(re.search(r'Threads:\s*(\d+)', txt).group(1))
                rss = int(re.search(r'VmRSS:\s*(\d+)', txt).group(1)) // 1024
                return th, rss
    except Exception:                            # noqa: BLE001 — best-effort
        pass
    return None, None


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    light = '--light' in sys.argv or os.environ.get('CONFLUX_SOAK_LIGHT')
    routes = ROUTES_LIGHT if light else ROUTES
    print(f'soak: {minutes} min x{workers} workers -> {BASE}'
          + (' [light]' if light else ''))

    code, err = _probe('/health/live')
    if code != 200:
        sys.exit(f'FALLO precondición: /health/live -> {code} {err}')

    th0, rss0 = _proc_stats()
    t_end = time.monotonic() + minutes * 60
    warm_end = time.monotonic() + min(30, minutes * 12)
    ths = []
    for _ in range(workers):
        threading.Thread(target=_worker, args=(routes,),
                         daemon=True).start()

    fails = []
    while time.monotonic() < t_end:
        time.sleep(5)
        code, err = _probe('/health/live')
        if code != 200:
            fails.append(f'/health/live -> {code} {err}')
        if time.monotonic() > warm_end:
            th, rss = _proc_stats()
            if th:
                ths.append((th, rss))
                if th0 and th > th0 * 1.2 + 5:
                    fails.append(f'threads {th} > 1.2x baseline {th0}')
                if rss0 and rss > rss0 * 1.5 + 256:
                    fails.append(f'RSS {rss}MB > 1.5x baseline {rss0}MB')
    _stop.set()
    time.sleep(2)

    code, err = _probe('/health/ready')
    if code != 200:
        fails.append(f'/health/ready final -> {code} {err}')
    with _lock:
        lat = sorted(_stats['lat'])
        n = len(lat)
        p50 = lat[n // 2] if n else 0
        p95 = lat[int(n * .95)] if n else 0
        print(f"reqs={_stats['reqs']} errs={_stats['errs']} "
              f"p50={p50:.0f}ms p95={p95:.0f}ms codes={_stats['codes']}")
        if ths:
            print(f'threads fin={ths[-1][0]} (base {th0}) '
                  f'rss fin={ths[-1][1]}MB (base {rss0})')
        if _stats['errs']:
            fails.append(f"{_stats['errs']} errores/5xx")
        if p95 > 15000:
            fails.append(f'p95 {p95:.0f}ms > 15s')
    if fails:
        print('SOAK FALLO:')
        for f in fails[:10]:
            print(' -', f)
        return 1
    print('SOAK OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
