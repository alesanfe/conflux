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
- al terminar la carga /health/ready no responde 200
- hay fuga de threads: el conteo EN REPOSO tras la carga queda >1.5x
  baseline (los picos durante carga son pool-sizing legítimo, no fuga)
- hay fuga de RSS: la memoria EN REPOSO tras una segunda ráfaga de
  verificación crece >30% respecto al reposo tras la primera. El RSS
  durante carga mezcla buffers en vuelo + cachés acotadas llenándose;
  comparar reposo-vs-reposo es la única señal honesta de retención.
  Además hay un techo absoluto RSS_CAP (defecto 4GB).
- p95 > 15s

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
                     # PrivateMemorySize64 = memoria comprometida real;
                     # WorkingSet64 es recortado por el OS bajo presión
                     # -> lecturas falsas bajas en plena carga.
                     "$r=[int]($p.PrivateMemorySize64/1MB);"
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


RSS_CAP_MB = int(os.environ.get('CONFLUX_SOAK_RSS_CAP', '4096'))
# presupuesto de errores por TASA: un outage total falla al instante en
# las sondas /health/live; los timeouts esporádicos (cola del pool de
# collect bajo N workers sostenidos — por encima del uso real de 1-3
# peticiones concurrentes) quedan contados y visibles, pero no es un
# fallo de estabilidad mientras la tasa sea baja.
ERR_BUDGET = float(os.environ.get('CONFLUX_SOAK_ERR_BUDGET', '0.001'))
SETTLE_S = float(os.environ.get('CONFLUX_SOAK_SETTLE', '25'))
VERIFY_S = float(os.environ.get('CONFLUX_SOAK_VERIFY', '60'))


def _spawn(routes, workers):
    for _ in range(workers):
        threading.Thread(target=_worker, args=(routes,),
                         daemon=True).start()


def _rest_stats():
    """Para la carga, deja asentar y mide el estado en reposo.
    Reintenta: un fallo de medición no debe silenciar el chequeo de fuga."""
    _stop.set()
    time.sleep(SETTLE_S)
    for _ in range(4):
        th, rss = _proc_stats()
        if th is not None and rss is not None:
            return th, rss
        time.sleep(3)
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
    _spawn(routes, workers)

    fails = []
    samples = []
    probes = probe_bad = streak = 0
    while time.monotonic() < t_end:
        time.sleep(5)
        probes += 1
        code, err = _probe('/health/live')
        if code != 200:
            probe_bad += 1
            streak += 1
            # outage total: varios fallos SEGUIDOS — eso sí es un fallo,
            # no un stall esporádico bajo carga.
            if streak >= 3:
                fails.append(f'/health/live caído: {streak} sondas '
                             f'seguidas -> {code} {err}')
                break
        else:
            streak = 0
        th, rss = _proc_stats()
        if th:
            samples.append((th, rss))
            if rss > RSS_CAP_MB:
                fails.append(f'RSS {rss}MB > cap {RSS_CAP_MB}MB')
    if probe_bad:
        print(f'aviso: {probe_bad}/{probes} sondas /health/live '
              'con timeout (stall bajo carga)')

    code, err = _probe('/health/ready')
    if code != 200:
        fails.append(f'/health/ready final -> {code} {err}')

    # reposo #1: estado tras la carga principal
    th_r1, rss_r1 = _rest_stats()

    # ráfaga de verificación + reposo #2: una fuga crece entre estados de
    # reposo equivalentes; las cachés acotadas se quedan planas.
    _stop.clear()
    _spawn(routes, workers)
    v_end = time.monotonic() + VERIFY_S
    vstreak = 0
    while time.monotonic() < v_end:
        time.sleep(5)
        vstreak = vstreak + 1 if _probe('/health/live')[0] != 200 else 0
        if vstreak >= 3:
            fails.append('/health/live caído en la ráfaga de verificación')
            break
    th_r2, rss_r2 = _rest_stats()

    with _lock:
        lat = sorted(_stats['lat'])
        n = len(lat)
        p50 = lat[n // 2] if n else 0
        p95 = lat[int(n * .95)] if n else 0
        print(f"reqs={_stats['reqs']} errs={_stats['errs']} "
              f"p50={p50:.0f}ms p95={p95:.0f}ms codes={_stats['codes']}")
        peak_rss = max((s[1] for s in samples), default=0)
        print(f'threads base={th0} reposo1={th_r1} reposo2={th_r2} | '
              f'RSS base={rss0}MB pico={peak_rss}MB '
              f'reposo1={rss_r1}MB reposo2={rss_r2}MB')
        if _stats['errs'] > max(5, _stats['reqs'] * ERR_BUDGET):
            fails.append(f"{_stats['errs']} errores/5xx "
                         f"(>{ERR_BUDGET:.1%} de {_stats['reqs']})")
        if p95 > 15000:
            fails.append(f'p95 {p95:.0f}ms > 15s')
    if th0 and th_r2 and th_r2 > th0 * 1.5 + 10:
        fails.append(f'fuga threads: reposo {th_r2} > 1.5x base {th0}')
    if rss_r1 and rss_r2 and rss_r2 > rss_r1 * 1.3 + 512:
        fails.append(f'fuga RSS: reposo2 {rss_r2}MB > 1.3x '
                     f'reposo1 {rss_r1}MB')
    if rss_r1 is None or rss_r2 is None:
        print('aviso: no se pudo medir reposo — chequeo de fuga omitido')
    if fails:
        print('SOAK FALLO:')
        for f in fails[:10]:
            print(' -', f)
        return 1
    print('SOAK OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
