"""unittest con timer + memoria por test, percentiles y baseline.

Uso:
    python scripts/test_times.py                 # top 15 mas lentos, suite completa
    python scripts/test_times.py 30              # top 30
    python scripts/test_times.py 15 tests/unit   # solo unit tests
    python scripts/test_times.py --all           # tiempo de TODOS los tests, ordenado
    python scripts/test_times.py --nomem         # sin medir memoria (mas rapido)
    python scripts/test_times.py --save base.json    # guarda baseline (tiempo+memoria)
    python scripts/test_times.py --compare base.json # compara contra baseline:
                                                 #   regresiones = test >1.5x y +0.3s
    python scripts/test_times.py --mem           # ranking por MEMORIA pico en vez de tiempo

Mide por test: duracion (perf_counter, incluye setUp/tearDown) y pico de
memoria (tracemalloc). Reporta ranking, percentiles p50/p95/p99, agregado
por paquete de tests y, con --compare, regresiones/nuevos/eliminados.
"""
import json
import sys
import time
import tracemalloc
import unittest


class TimedResult(unittest.TextTestResult):
    """Result que registra duracion y memoria pico por test (en `times`)."""

    times: dict = {}
    mems: dict = {}
    with_mem = True

    def startTest(self, test):
        self._t0 = time.perf_counter()
        if self.with_mem:
            tracemalloc.start()
            self._m0 = tracemalloc.get_traced_memory()[0]
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        self.times[test.id()] = time.perf_counter() - self._t0
        if self.with_mem:
            cur, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.mems[test.id()] = max(0, peak - self._m0)


def _pct(sorted_vals, q):
    """Percentil q (0-100) de una lista ya ordenada; [min] si corta."""
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * q / 100))]


def _by_pkg(tid):
    """'tests.unit.test_x.T.test_y' -> 'unit'; otros -> paquete real."""
    parts = tid.split('.')
    return parts[1] if len(parts) > 2 and parts[0] == 'tests' else parts[0]


class TimedRunner(unittest.TextTestRunner):
    """Runner con ranking, percentiles, agregado por paquete y baseline."""

    resultclass = TimedResult
    top = 15
    show_all = False
    by_mem = False
    save_to = None
    compare_to = None

    def run(self, test):
        self.resultclass.times = {}
        self.resultclass.mems = {}
        t0 = time.perf_counter()
        res = super().run(test)
        total = time.perf_counter() - t0
        times, mems = self.resultclass.times, self.resultclass.mems
        w = self.stream.writeln

        # ---- resumen global ----
        vals = sorted(times.values())
        w(f"\n=== {len(vals)} tests en {total:.1f}s ===")
        if vals:
            w(f"  p50 {_pct(vals,50)*1000:.0f}ms  p95 {_pct(vals,95):.2f}s  "
              f"p99 {_pct(vals,99):.2f}s  max {vals[-1]:.2f}s  "
              f"suma {sum(vals):.1f}s")
        if mems:
            mv = sorted(mems.values())
            w(f"  memoria pico: p50 {mv[len(mv)//2]/1024:.0f}KB  "
              f"max {mv[-1]/1024/1024:.1f}MB")

        # ---- agregado por paquete ----
        pkgs: dict = {}
        for tid, el in times.items():
            p = pkgs.setdefault(_by_pkg(tid), {'n': 0, 't': 0.0, 'max': (0.0, '')})
            p['n'] += 1
            p['t'] += el
            if el > p['max'][0]:
                p['max'] = (el, tid)
        w("\n--- por paquete (tests, total, mas lento) ---")
        for name, p in sorted(pkgs.items(), key=lambda x: -x[1]['t']):
            slow = p['max'][1].rsplit('.', 1)[-1]
            w(f"  {name:<12} {p['n']:>3} tests  {p['t']:7.2f}s  "
              f"({slow} {p['max'][0]:.2f}s)")

        # ---- ranking ----
        if self.by_mem and mems:
            items = sorted(mems.items(), key=lambda x: -x[1])
            n = len(items) if self.show_all else self.top
            w(f"\n--- memoria pico por test ---")
            for tid, m in items[:n]:
                w(f"{m/1024/1024:7.2f}MB  {times.get(tid, 0):6.3f}s  {tid}")
        else:
            items = sorted(times.items(), key=lambda x: -x[1])
            n = len(items) if self.show_all else self.top
            w(f"\n--- tiempos por test ---")
            for tid, el in items[:n]:
                mem = f" {mems[tid]/1024:6.0f}KB" if tid in mems else ''
                w(f"{el:7.3f}s{mem}  {tid}")
            if not self.show_all and len(items) > self.top:
                w(f"  ... ({len(items) - self.top} mas; --all para verlos todos)")

        # ---- baseline: save / compare ----
        if self.save_to:
            with open(self.save_to, 'w', encoding='utf-8') as f:
                json.dump({'times': times, 'mems': mems}, f)
            w(f"\nbaseline guardada en {self.save_to}")
        if self.compare_to:
            try:
                base = json.load(open(self.compare_to, encoding='utf-8'))
            except OSError:
                w(f"\n[compare] no existe {self.compare_to} — usa --save primero")
                return res
            bt = base.get('times', {})
            # factor de carga global: si la maquina va un 40% mas lenta, TODOS
            # los tests suben igual — no es regresion. Solo marca los que
            # superan la inflacion mediana de la suite.
            import statistics as _st
            ratios = [el / bt[tid] for tid, el in times.items()
                      if bt.get(tid, 0) > 0.05]
            factor = _st.median(ratios) if ratios else 1.0
            regr = [(tid, bt[tid], el) for tid, el in times.items()
                    if tid in bt and el - bt[tid] * factor > 0.3
                    and el > 1.4 * bt[tid] * factor]
            gone = sorted(set(bt) - set(times))
            new = sorted(set(times) - set(bt))
            w(f"\n--- vs baseline {self.compare_to} "
              f"(factor carga x{factor:.2f}) ---")
            if regr:
                w(f"  REGRESIONES ({len(regr)}):")
                for tid, b, el in sorted(regr, key=lambda x: -(x[2]-x[1])):
                    w(f"    {b:.2f}s -> {el:.2f}s  {tid}")
            else:
                w("  sin regresiones de tiempo")
            w(f"  {len(new)} tests nuevos, {len(gone)} eliminados")
        return res


def main():
    args = sys.argv[1:]
    show_all = '--all' in args
    by_mem = '--mem' in args
    save_to = args[args.index('--save') + 1] if '--save' in args else None
    compare_to = (args[args.index('--compare') + 1]
                  if '--compare' in args else None)
    args = [a for a in args if not a.startswith('--')
            and a not in (save_to, compare_to)]
    top = int(args[0]) if args and args[0].isdigit() else 15
    start = args[1] if len(args) > 1 else 'tests'
    TimedResult.with_mem = '--nomem' not in sys.argv
    suite = unittest.TestLoader().discover(start, top_level_dir='.')
    runner = TimedRunner(verbosity=1)
    runner.top = top
    runner.show_all = show_all
    runner.by_mem = by_mem
    runner.save_to = save_to
    runner.compare_to = compare_to
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
