"""Gate de cobertura por archivo (scripts/cov_gate.py): filtro src/, umbral,
diagnostico de ramas, exit codes."""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
import cov_gate  # noqa: E402


def _file(pct, missing_lines=None, mb=0, pairs=None):
    """Entrada coverage json sintética para un fichero."""
    return {'summary': {'percent_covered': pct, 'missing_branches': mb,
                        'percent_branches_covered': 50.0},
            'missing_lines': missing_lines or [],
            'missing_branches': pairs or []}


def _cov(files, total=99.0, tb=95.0):
    return json.dumps({'files': files,
                       'totals': {'percent_covered': total,
                                  'percent_branches_covered': tb}})


def _run(argv, run_ret):
    """main() con sys.argv y subprocess.run controlados -> (exit_code, stdout)."""
    out = io.StringIO()
    rc = 0
    with mock.patch.object(sys, 'argv', argv), \
            mock.patch('subprocess.run', return_value=run_ret), \
            redirect_stdout(out):
        try:
            cov_gate.main()
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
    return rc, out.getvalue()


class TestSrcFiles(unittest.TestCase):
    def test_solo_src_no_init_ni_proxy(self):
        files = {'src/a.py': 1, 'src/b/__init__.py': 2, 'proxy.py': 3,
                 'tests/t.py': 4, 'src\\win.py': 5}
        self.assertEqual(set(cov_gate._src_files(files)), {'src/a.py', 'src\\win.py'})


class TestGate(unittest.TestCase):
    def test_todo_ok(self):
        ok = mock.Mock(returncode=0, stdout=_cov({
            'src/a.py': _file(90.0), 'src/b.py': _file(80.0)}), stderr='')
        rc, out = _run(['cov_gate.py'], ok)
        self.assertEqual(rc, 0)
        self.assertIn('OK', out)
        self.assertNotIn('bajo el umbral', out)

    def test_bajo_umbral_exit_1_y_lista(self):
        ok = mock.Mock(returncode=0, stdout=_cov({
            'src/ok.py': _file(90.0),
            'src/malo.py': _file(79.9, missing_lines=[10, 11, 12])}), stderr='')
        rc, out = _run(['cov_gate.py'], ok)
        self.assertEqual(rc, 1)
        self.assertIn('src/malo.py', out)
        self.assertNotIn('src/ok.py  (lineas', out)
        self.assertIn('10', out)                     # missing_lines or [] (Or@61)

    def test_missing_lines_cap_12(self):
        """missing[:12] — 13 lineas sin cubrir muestra 12 (mutante 12+1@70)."""
        ok = mock.Mock(returncode=0, stdout=_cov({
            'src/m.py': _file(50.0,
                              missing_lines=list(range(10, 23)))}), stderr='')
        rc, out = _run(['cov_gate.py'], ok)
        self.assertEqual(rc, 1)
        self.assertIn('21', out)                     # 12ª línea sí se lista
        self.assertNotIn('22', out)                  # la 13ª queda fuera

    def test_subprocess_kwargs(self):
        """coverage json se pide con capture_output/text/check exactos —
        mutantes True@51/False@51."""
        ok = mock.Mock(returncode=0, stdout=_cov({'src/a.py': _file(90)}),
                       stderr='')
        with mock.patch.object(sys, 'argv', ['cov_gate.py']), \
                mock.patch('subprocess.run', return_value=ok) as m, \
                redirect_stdout(io.StringIO()):
            cov_gate.main()
        m.assert_called_once_with(
            [sys.executable, '-m', 'coverage', 'json', '-o', '-'],
            capture_output=True, text=True, check=False)

    def test_borde_exacto_pasa(self):
        """pct < umbral falla; pct == umbral pasa (Lt@66 mutado a <=)."""
        ok = mock.Mock(returncode=0, stdout=_cov({
            'src/borde.py': _file(80.0)}), stderr='')
        rc, _ = _run(['cov_gate.py'], ok)
        self.assertEqual(rc, 0)

    def test_umbral_cli(self):
        ok = mock.Mock(returncode=0, stdout=_cov({
            'src/a.py': _file(85.0)}), stderr='')
        rc, _ = _run(['cov_gate.py', '90'], ok)
        self.assertEqual(rc, 1)

    def test_coverage_json_falla_exit(self):
        fail = mock.Mock(returncode=2, stdout='', stderr='boom')
        rc, _ = _run(['cov_gate.py'], fail)
        self.assertNotEqual(rc, 0)

    def test_branches_diag_en_exito(self):
        ok = mock.Mock(returncode=0, stdout=_cov({
            'src/a.py': _file(95.0, mb=3, pairs=[[10, 12]])}), stderr='')
        rc, out = _run(['cov_gate.py', '--branches'], ok)
        self.assertEqual(rc, 0)
        self.assertIn('10->12', out)

    def test_branches_diag_orden_top(self):
        """_report_branches ordena por missing_branches desc (Sub@91 lo
        invertiría) y corta a `top` (Lt@… [:top])."""
        out = io.StringIO()
        files = {'src/%s.py' % n: _file(90.0, mb=i)
                 for i, n in enumerate('abcdefg')}
        with redirect_stdout(out):
            cov_gate._report_branches(files, top=3)
        text = out.getvalue()
        i6, i5, i4 = (text.index('src/g.py'), text.index('src/f.py'),
                      text.index('src/e.py'))
        self.assertLess(i6, i5)
        self.assertLess(i5, i4)
        self.assertNotIn('src/d.py', text)          # top=3 corta el resto
        self.assertNotIn('src/a.py', text)          # mb=0 no se lista

    def test_sin_ramas_no_lista_detalle(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cov_gate._report_branches({'src/a.py': _file(90.0, mb=0)})
        self.assertNotIn('src/a.py', out.getvalue().splitlines()[2:])

    def test_top_default_6(self):
        """top=6 por defecto — 8 ficheros muestra solo los 6 peores
        (mutante 6+1@28)."""
        out = io.StringIO()
        files = {'src/%s.py' % n: _file(90.0, mb=i)
                 for i, n in enumerate('abcdefgh')}
        with redirect_stdout(out):
            cov_gate._report_branches(files)
        text = out.getvalue()
        self.assertLess(text.index('src/h.py'), text.index('src/f.py'))
        self.assertNotIn('src/b.py', text)
        self.assertNotIn('src/a.py', text)

    def test_pares_cap_8(self):
        """missing_branches[:8] — 9 pares imprime 8 (mutante 8+1@40)."""
        out = io.StringIO()
        pairs = [[i, i + 1] for i in range(10, 19)]
        with redirect_stdout(out):
            cov_gate._report_branches({'src/a.py': _file(90.0, mb=9,
                                                         pairs=pairs)})
        self.assertIn('17->18', out.getvalue())
        self.assertNotIn('18->19', out.getvalue())


if __name__ == '__main__':
    unittest.main()
