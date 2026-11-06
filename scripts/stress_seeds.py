#!/usr/bin/env python3
"""Stress de orden/repetición (CONFLUX-028): corre la suite con N seeds
distintos barajando el orden de los tests. Falla si algún seed deja
errores — imprime el seed para reproducir: --seed N."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flat(t)
        else:
            yield t


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3,
                    help="nº de seeds aleatorios a probar")
    ap.add_argument("--seed", type=int, help="seed fija (reproducir)")
    ap.add_argument("--top", default="tests/unit",
                    help="dir de tests (default tests/unit: rápido)")
    args = ap.parse_args()

    loader = unittest.TestLoader()
    suite = loader.discover(args.top, top_level_dir=".")
    all_tests = list(_flat(suite))
    print(f"{len(all_tests)} tests", flush=True)

    seeds = [args.seed] if args.seed is not None else [
        random.SystemRandom().randrange(1 << 31) for _ in range(args.seeds)]
    failures = 0
    for seed in seeds:
        rng = random.Random(seed)
        order = list(all_tests)
        rng.shuffle(order)
        s = unittest.TestSuite(order)
        res = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(s)
        status = "OK" if res.wasSuccessful() else "FAIL"
        print(f"seed={seed}: {status} ({res.testsRun} tests)", flush=True)
        if not res.wasSuccessful():
            failures += 1
    print("reproduce: stress_seeds.py --seed <N>", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
