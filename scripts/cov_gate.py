"""Gate de cobertura POR ARCHIVO: cada fichero de src/ >= umbral (default 80%).

Uso:
    coverage run -m unittest discover -s tests -t .
    python scripts/cov_gate.py            # falla si algun archivo <80%
    python scripts/cov_gate.py 90         # otro umbral
    python scripts/cov_gate.py --branches # top ficheros por ramas sin cubrir
    python scripts/cov_gate.py 80 --branches

El % evaluado es el COMBINADO (lineas+ramas) — pyproject activa branch=true,
asi un 'if' ejercitado solo por un lado cuenta como rama parcial.
--branches no cambia el gate: imprime diagnostico de donde estan las
ramas sin ejercitar (pares linea_origen->linea_destino).

Exit 0 si todo pasa; exit 1 listando los ficheros bajo el umbral.
"""
import json
import subprocess
import sys


def _src_files(files):
    return {p: i for p, i in files.items()
            if (p.startswith('src' + '\\') or p.startswith('src/'))
            and not p.endswith('__init__.py') and p != 'proxy.py'}


def _report_branches(files, top=6):
    """Ficheros src/ con mas ramas parciales — los huecos que la
    cobertura de linea no ve."""
    worst = sorted(_src_files(files).items(),
                   key=lambda kv: -kv[1]['summary'].get('missing_branches', 0))
    print(f'\ntop {top} por ramas sin ejercitar (origen->destino):')
    for path, info in worst[:top]:
        s = info['summary']
        mb = s.get('missing_branches', 0)
        if not mb:
            continue
        pairs = ' '.join(f'{a}->{b}' for a, b in
                         (info.get('missing_branches') or [])[:8])
        print(f"  {s.get('percent_branches_covered', 0):5.1f}% ramas "
              f"({mb} sin cubrir)  {path}")
        if pairs:
            print(f'          {pairs}')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    umbral = float(args[0]) if args else 80.0
    r = subprocess.run([sys.executable, '-m', 'coverage', 'json', '-o', '-'],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        sys.exit('coverage json fallo — corre primero: '
                 'coverage run -m unittest discover -s tests -t .')
    data = json.loads(r.stdout)
    files = _src_files(data.get('files', {}))
    malos = []
    for path, info in sorted(files.items()):
        pct = info['summary']['percent_covered']
        if pct < umbral:
            missing = info.get('missing_lines') or []
            malos.append((path, pct, missing))
    total = data.get('totals', {}).get('percent_covered', 0)
    tb = data.get('totals', {}).get('percent_branches_covered', 0)
    print(f'cobertura total: {total:.1f}%  ramas: {tb:.1f}%  '
          f'(gate por archivo: {umbral:.0f}%)')
    if malos:
        print(f'\n{len(malos)} archivo(s) bajo el umbral:')
        for path, pct, missing in malos:
            print(f'  {pct:5.1f}%  {path}  (lineas sin cubrir: {missing[:12]})')
        _report_branches(data.get('files', {}))
        sys.exit(1)
    print('OK — todos los archivos >= umbral')
    if '--branches' in sys.argv:
        _report_branches(data.get('files', {}))


if __name__ == '__main__':
    main()
