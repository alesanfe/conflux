"""unittest con timer por test: mide cada uno y reporta los N mas lentos.

Uso:
    python scripts/test_times.py            # top 15 mas lentos, suite completa
    python scripts/test_times.py 30         # top 30
    python scripts/test_times.py 15 tests/unit   # solo unit tests
    python scripts/test_times.py --all      # tiempo de TODOS los tests, ordenado
"""
import sys
import time
import unittest


class TimedResult(unittest.TextTestResult):
    """Result que registra duracion por test (acumulada en `times`)."""

    times: dict = {}

    def startTest(self, test):
        self._t0 = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        self.times[test.id()] = time.perf_counter() - self._t0


class TimedRunner(unittest.TextTestRunner):
    """Runner que imprime el ranking de tests mas lentos al final."""

    resultclass = TimedResult
    top = 15
    show_all = False

    def run(self, test):
        self.resultclass.times = {}
        t0 = time.perf_counter()
        res = super().run(test)
        total = time.perf_counter() - t0
        items = sorted(self.resultclass.times.items(), key=lambda x: -x[1])
        n = len(items) if self.show_all else self.top
        self.stream.writeln(
            f"\n--- tiempos por test ({len(items)} tests, {total:.1f}s) ---")
        for tid, el in items[:n]:
            self.stream.writeln(f"{el:7.3f}s  {tid}")
        if not self.show_all and len(items) > self.top:
            self.stream.writeln(f"  ... ({len(items) - self.top} mas; "
                                "--all para verlos todos)")
        return res


def main():
    args = [a for a in sys.argv[1:] if a != '--all']
    show_all = '--all' in sys.argv
    top = int(args[0]) if args and args[0].isdigit() else 15
    start = args[1] if len(args) > 1 else 'tests'
    suite = unittest.TestLoader().discover(start, top_level_dir='.')
    runner = TimedRunner(verbosity=1)
    runner.top = top
    runner.show_all = show_all
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
