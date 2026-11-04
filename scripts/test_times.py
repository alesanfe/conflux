"""unittest con timer + memoria por test, percentiles y baseline.

Uso:
    python scripts/test_times.py                 # top 15 mas lentos, suite completa
    python scripts/test_times.py 30              # top 30
    python scripts/test_times.py 15 tests/unit   # solo unit tests
    python scripts/test_times.py --all           # tiempo de TODOS los tests, ordenado
    python scripts/test_times.py --jobs 9        # paralelo: bin-packing LPT de ficheros
                                                 #   por peso de baseline. Util si la suite
                                                 #   vuelve a >20s; a ~8s serial el spawn+
                                                 #   contencion cuestan mas de lo que ganan
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


def _report(w, times, mems, total, top=15, show_all=False, by_mem=False,
            save_to=None, compare_to=None):
    """Resumen completo (global, paquete, ranking, baseline) — usado por
    la corrida serial y por la fusion de workers --jobs."""
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

    if by_mem and mems:
        items = sorted(mems.items(), key=lambda x: -x[1])
        n = len(items) if show_all else top
        w("\n--- memoria pico por test ---")
        for tid, m in items[:n]:
            w(f"{m/1024/1024:7.2f}MB  {times.get(tid, 0):6.3f}s  {tid}")
    else:
        items = sorted(times.items(), key=lambda x: -x[1])
        n = len(items) if show_all else top
        w("\n--- tiempos por test ---")
        for tid, el in items[:n]:
            mem = f" {mems[tid]/1024:6.0f}KB" if tid in mems else ''
            w(f"{el:7.3f}s{mem}  {tid}")
        if not show_all and len(items) > top:
            w(f"  ... ({len(items) - top} mas; --all para verlos todos)")

    if save_to:
        with open(save_to, 'w', encoding='utf-8') as f:
            json.dump({'times': times, 'mems': mems}, f)
        w(f"\nbaseline guardada en {save_to}")
    if compare_to:
        try:
            with open(compare_to, encoding='utf-8') as f:
                base = json.load(f)
        except OSError:
            w(f"\n[compare] no existe {compare_to} — usa --save primero")
            return
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
        w(f"\n--- vs baseline {compare_to} "
          f"(factor carga x{factor:.2f}) ---")
        if regr:
            w(f"  REGRESIONES ({len(regr)}):")
            for tid, b, el in sorted(regr, key=lambda x: -(x[2]-x[1])):
                w(f"    {b:.2f}s -> {el:.2f}s  {tid}")
        else:
            w("  sin regresiones de tiempo")
        w(f"  {len(new)} tests nuevos, {len(gone)} eliminados")


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
        _report(self.stream.writeln, self.resultclass.times,
                self.resultclass.mems, time.perf_counter() - t0,
                top=self.top, show_all=self.show_all, by_mem=self.by_mem,
                save_to=self.save_to, compare_to=self.compare_to)
        return res


def _run_json(start, out_path, with_mem, jobfile=None):
    """Worker --jobs: corre `start` (o los items de `jobfile`) y vuelca
    times/mems/fallos a JSON."""
    import os
    sys.path.insert(0, os.getcwd())   # scripts/ es sys.path[0]; 'tests.*' vive en cwd
    TimedResult.with_mem = with_mem
    TimedResult.times, TimedResult.mems = {}, {}
    loader = unittest.TestLoader()
    if jobfile:
        suite = unittest.TestSuite()
        with open(jobfile, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if os.path.isdir(line):
                    suite.addTests(loader.discover(line, top_level_dir='.'))
                else:
                    suite.addTests(loader.loadTestsFromName(line))
    else:
        suite = loader.discover(start, top_level_dir='.')
    res = unittest.TextTestRunner(resultclass=TimedResult,
                                  verbosity=0).run(suite)
    failed = [t.id() for t, _ in res.failures + res.errors]
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'times': TimedResult.times, 'mems': TimedResult.mems,
                   'failed': failed, 'testsRun': res.testsRun}, f)
    return 0 if res.wasSuccessful() else 1


def _test_modules(start):
    """Modulos test_*.py bajo `start` -> nombres dotted (tests.unit.test_x).
    El FICHERO es la unidad de sharding: un import+setup por worker
    (sharding por test-id repite imports y dispara la contencion)."""
    import os
    mods = []
    for root, _, files in os.walk(start):
        for f in sorted(files):
            if f.startswith('test') and f.endswith('.py'):
                mods.append(os.path.join(root, f)[:-3]
                          .replace(os.sep, '.').replace('/', '.'))
    return mods


def _plan_jobs(jobs, start, weights):
    """Chunks para --jobs. Con baseline: bin-packing LPT de MODULOS por
    peso esperado. Sin baseline: un chunk por paquete."""
    import os
    if weights:
        items = []
        for mod in _test_modules(start):
            w = sum(t for tid, t in weights.items()
                    if tid.startswith(mod + '.'))
            items.append((w, mod))
        med = sorted(w for w, _ in items if w) or [0.3]
        default = med[len(med) // 2]                       # ficheros nuevos
        bins: list = [[] for _ in range(jobs)]
        load = [0.0] * jobs
        for w, mod in sorted(items, key=lambda x: -x[0]):
            i = load.index(min(load))
            bins[i].append(mod)
            load[i] += w or default
        return [b for b in bins if b]
    dirs = sorted(
        os.path.join(start, d) for d in os.listdir(start)
        if os.path.isdir(os.path.join(start, d))
        and any(f.startswith('test') and f.endswith('.py')
                for f in os.listdir(os.path.join(start, d))))
    return [[d] for d in dirs] or [[start]]


def _run_jobs(jobs, start, with_mem, weights_path=None):
    """Paralelo: subproceso por chunk de trabajo, fusiona JSONs."""
    import concurrent.futures as cf
    import os
    import subprocess
    import tempfile
    weights = {}
    if weights_path and os.path.exists(weights_path):
        try:
            with open(weights_path, encoding='utf-8') as f:
                weights = json.load(f).get('times', {})
        except (OSError, ValueError):
            pass
    chunks = _plan_jobs(jobs, start, weights)
    tmp = tempfile.mkdtemp(prefix='ttimes-')
    cmd_base = [sys.executable, os.path.abspath(__file__), '--json']
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {}
        for i, chunk in enumerate(chunks):
            out = os.path.join(tmp, f'w{i}.json')
            job = os.path.join(tmp, f'w{i}.txt')
            with open(job, 'w', encoding='utf-8') as f:
                f.write('\n'.join(chunk))
            futs[ex.submit(subprocess.run,
                           cmd_base + [out, start, '--jobfile', job]
                           + ([] if with_mem else ['--nomem']),
                           capture_output=True)] = out
        times, mems, failed = {}, {}, []
        rc = 0
        for fut in cf.as_completed(futs):
            r = fut.result()
            out = futs[fut]
            if r.returncode != 0:
                rc = 1
                sys.stderr.write(r.stderr.decode('utf-8', 'replace')[-3000:])
            try:
                with open(out, encoding='utf-8') as f:
                    d = json.load(f)
            except OSError:
                continue
            times.update(d['times'])
            mems.update(d['mems'])
            failed += d['failed']
    total = time.perf_counter() - t0
    if failed:
        print('\nFALLOS:', file=sys.stderr)
        for tid in failed:
            print(f'  {tid}', file=sys.stderr)
    return times, mems, total, rc


def main():
    args = sys.argv[1:]
    show_all = '--all' in args
    by_mem = '--mem' in args
    with_mem = '--nomem' not in args
    save_to = args[args.index('--save') + 1] if '--save' in args else None
    compare_to = (args[args.index('--compare') + 1]
                  if '--compare' in args else None)
    json_out = args[args.index('--json') + 1] if '--json' in args else None
    jobfile = args[args.index('--jobfile') + 1] if '--jobfile' in args else None
    jobs_v = args[args.index('--jobs') + 1] if '--jobs' in args else None
    jobs = int(jobs_v) if jobs_v else 0
    consumed = {a for a in (save_to, compare_to, json_out, jobfile, jobs_v) if a}
    pos = [a for a in args if not a.startswith('--') and a not in consumed]
    top = int(pos[0]) if pos and pos[0].isdigit() else 15
    start = (pos[1] if len(pos) > 1
             else pos[0] if pos and not pos[0].isdigit() else 'tests')
    TimedResult.with_mem = with_mem

    if json_out:                              # modo worker: vuelca y calla
        sys.exit(_run_json(start, json_out, with_mem, jobfile))
    if jobs:                                  # paralelo por paquete
        weights_src = compare_to or save_to or '.test-baseline.json'
        times, mems, total, rc = _run_jobs(jobs, start, with_mem,
                                           weights_path=weights_src)
        _report(print, times, mems, total, top=top, show_all=show_all,
                by_mem=by_mem, save_to=save_to, compare_to=compare_to)
        sys.exit(rc)

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
