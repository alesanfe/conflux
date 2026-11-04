"""Gate de cobertura POR ARCHIVO: cada fichero de src/ >= umbral (default 80%).

Uso:
    coverage run -m unittest discover -s tests -t .
    python scripts/cov_gate.py            # falla si algun archivo <80%
    python scripts/cov_gate.py 90         # otro umbral

Exit 0 si todo pasa; exit 1 listando los ficheros bajo el umbral.
"""
import json
import subprocess
import sys


def main():
    umbral = float(sys.argv[1]) if len(sys.argv) > 1 else 80.0
    r = subprocess.run([sys.executable, '-m', 'coverage', 'json', '-o', '-'],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        sys.exit('coverage json fallo — corre primero: '
                 'coverage run -m unittest discover -s tests -t .')
    data = json.loads(r.stdout)
    files = data.get('files', {})
    malos = []
    for path, info in sorted(files.items()):
        if not (path.startswith('src' + '\\') or path.startswith('src/')) \
                or path.endswith('__init__.py') or path == 'proxy.py':
            continue
        pct = info['summary']['percent_covered']
        if pct < umbral:
            missing = info.get('missing_lines') or []
            malos.append((path, pct, missing))
    total = data.get('totals', {}).get('percent_covered', 0)
    print(f'cobertura total: {total:.1f}%  (gate por archivo: {umbral:.0f}%)')
    if malos:
        print(f'\n{len(malos)} archivo(s) bajo el umbral:')
        for path, pct, missing in malos:
            print(f'  {pct:5.1f}%  {path}  (lineas sin cubrir: {missing[:12]})')
        sys.exit(1)
    print('OK — todos los archivos >= umbral')


if __name__ == '__main__':
    main()
