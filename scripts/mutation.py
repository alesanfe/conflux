"""Mutation testing local (Windows-safe, sin deps) + wrapper mutmut en CI/Linux.

Aplica UN mutante por vez sobre el archivo objetivo, corre sus tests
enfocados y clasifica: KILLED (tests fallan = el mutante fue detectado),
SURVIVED (tests pasan = hueco de asercion), TIMEOUT/ERROR.

Operadores: comparacion (== <-> !=, < <-> <=, > <-> >=, in <-> not in),
aritmetica (+ <-> -, * <-> //), booleanos (and <-> or, True <-> False,
not eliminado), constantes numericas +-1, return None.

Uso:
    python scripts/mutation.py src/conflux/infra/epg.py tests/unit/test_epg.py
    python scripts/mutation.py src/conflux/infra/anilist.py   # autodetecta tests
    python scripts/mutation.py archivo.py tests/x.py --max 30 --timeout 20
"""
import ast
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FOCUS = {
    'infra/epg.py': 'tests/unit/test_epg.py',
    'infra/torrent_health.py': 'tests/unit/test_torrent_health.py',
    'infra/anilist.py': 'tests/unit/test_anilist.py',
    'infra/torznab.py': 'tests/unit/test_torznab.py',
    'infra/calendar.py': 'tests/unit/test_calendar.py',
    'domain/calendar.py': 'tests/unit/test_calendar.py',
    'domain/catalog.py': 'tests/unit/test_catalog.py',
    'domain/text.py': 'tests/unit/test_text.py',
    'domain/lists.py': 'tests/unit/test_lists.py',
    'domain/manifest.py': 'tests/unit/test_manifest.py',
    'domain/metafix.py': 'tests/unit/test_metafix.py',
    'domain/sources.py': 'tests/unit/test_sources.py',
    'app/subs.py': 'tests/unit/test_subs.py',
    'app/bg.py': 'tests/unit/test_bg.py',
    'app/meta.py': 'tests/unit/test_meta.py',
    'app/stream.py': 'tests/unit/test_stream.py',
    'web/server.py': 'tests/unit/test_server.py',
    'web/pages.py': 'tests/unit/test_pages.py',
    'infra/net.py': 'tests/unit/test_net.py',
    'infra/debrid.py': 'tests/unit/test_debrid.py',
    'infra/trakt.py': 'tests/unit/test_trakt.py',
    'infra/mediafusion.py': 'tests/unit/test_mediafusion.py',
    'infra/wikidata.py': 'tests/unit/test_wikidata.py',
    'infra/easynews.py': 'tests/unit/test_easynews.py',
    'infra/scrape.py': 'tests/unit/test_scrape.py',
    'infra/zilean.py': 'tests/unit/test_zilean.py',
    'infra/newznab.py': 'tests/unit/test_newznab.py',
    'core/cfg.py': 'tests/unit/test_cfg.py',
    'core/state.py': 'tests/unit/test_state.py',
}

_CMP_SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
             ast.Lt: ast.LtE, ast.LtE: ast.Lt,
             ast.Gt: ast.GtE, ast.GtE: ast.Gt,
             ast.In: ast.NotIn, ast.NotIn: ast.In,
             ast.Is: ast.IsNot, ast.IsNot: ast.Is}
_BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add,
             ast.Mult: ast.Div, ast.Div: ast.Mult,
             ast.Mod: ast.Div}


class _Hunter(ast.NodeTransformer):
    """Un mutante por pasada: el primero que aplica (index n) gana."""

    def __init__(self, target):
        self.target = target          # indice de mutante a aplicar
        self.n = -1                   # contador de mutantes posibles
        self.desc = None

    def _try(self, node, kind, new_node):
        self.n += 1
        if self.n == self.target:
            self.desc = f'{kind}@{node.lineno}'
            return new_node
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        ops = [self._try_op(node, op) for op in node.ops]
        if self.desc and self.n == self.target:
            node.ops = ops
        return node

    def _try_op(self, node, op):
        self.n += 1
        if self.n == self.target and type(op) in _CMP_SWAP:
            self.desc = f'cmp {type(op).__name__}@{node.lineno}'
            return _CMP_SWAP[type(op)]()
        return op

    def visit_BinOp(self, node):
        self.generic_visit(node)
        self.n += 1
        if self.n == self.target and type(node.op) in _BIN_SWAP:
            self.desc = f'binop {type(node.op).__name__}@{node.lineno}'
            node.op = _BIN_SWAP[type(node.op)]()
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        self.n += 1
        if self.n == self.target and isinstance(node.op, (ast.And, ast.Or)):
            self.desc = f'boolop {type(node.op).__name__}@{node.lineno}'
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not):
            self.n += 1
            if self.n == self.target:
                self.desc = f'not-elim@{node.lineno}'
                return node.operand
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            self.n += 1
            if self.n == self.target:
                self.desc = f'const {node.value}@{node.lineno}'
                return ast.copy_location(ast.Constant(value=not node.value), node)
        elif isinstance(node.value, (int, float)) and node.value not in (0, 1):
            self.n += 1
            if self.n == self.target:
                self.desc = f'const {node.value}+1@{node.lineno}'
                return ast.copy_location(ast.Constant(value=node.value + 1), node)
        return node


def count_mutants(tree):
    h = _Hunter(-1)
    h.visit(tree)
    return h.n + 1


def apply_mutation(tree, idx):
    h = _Hunter(idx)
    h.visit(tree)
    return h.desc


def main():
    raw = sys.argv[1:]
    args, maxn, timeout = [], 40, 20
    i = 0
    while i < len(raw):
        if raw[i] in ('--max', '--timeout'):
            if raw[i] == '--max':
                maxn = int(raw[i + 1])
            else:
                timeout = int(raw[i + 1])
            i += 2
        else:
            args.append(raw[i])
            i += 1
    if not args:
        sys.exit('uso: python scripts/mutation.py <modulo.py> [tests.py]')
    mod = Path(args[0])
    if not mod.exists():
        mod = Path('src/conflux') / args[0]
    tests = args[1] if len(args) > 1 else FOCUS.get(
        str(mod).replace('\\', '/').split('conflux/')[-1], 'tests/unit')
    src = mod.read_text(encoding='utf-8')
    total = count_mutants(ast.parse(src))
    print(f'{mod}: {total} mutantes posibles, tests: {tests} '
          f'(max {maxn}, timeout {timeout}s)')

    results = {'killed': [], 'survived': [], 'error': []}
    tmp = Path(tempfile.mkdtemp(prefix='mut_')) / mod.name
    for i in range(min(total, maxn)):
        t = ast.parse(src)
        desc = apply_mutation(t, i)
        if desc is None:
            continue
        try:
            tmp.write_text(ast.unparse(t), encoding='utf-8')
        except Exception as e:                       # mutante no-compila
            results['error'].append((desc, f'unparse: {e}'))
            continue
        shutil.copy2(tmp, mod)
        try:
            modname = tests.replace('/', '.').replace('\\', '.')
            if modname.endswith('.py'):
                modname = modname[:-3]
            r = subprocess.run(
                [sys.executable, '-m', 'unittest', '-q', modname],
                capture_output=True, timeout=timeout, cwd=Path.cwd())
            out = (r.stdout or b'') + (r.stderr or b'')
            if r.returncode == 0:
                results['survived'].append((desc, ''))
            elif b'FAILED' in out or b'ERROR' in out:
                results['killed'].append((desc, ''))
            else:                                    # corrida rota, no mutante
                results['error'].append((desc, 'test run failed'))
        except subprocess.TimeoutExpired:
            results['error'].append((desc, 'timeout'))
        finally:
            mod.write_text(src, encoding='utf-8')    # restaurar SIEMPRE
    k, s, e = len(results['killed']), len(results['survived']), len(results['error'])
    print(f'\n=== {k + s + e} mutantes: {k} killed, {s} survived, {e} err/timeout ===')
    for d, _ in results['survived']:
        print(f'  SURVIVED  {d}')
    for d, why in results['error']:
        print(f'  ERROR     {d} ({why})')
    if k + s:
        print(f'kill-rate: {k / (k + s) * 100:.0f}%')


if __name__ == '__main__':
    main()
