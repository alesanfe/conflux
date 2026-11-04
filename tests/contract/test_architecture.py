# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position,too-few-public-methods
"""Contrato de arquitectura: las capas documentadas en AGENTS.md se cumplen
(web -> app -> domain -> infra -> core). import-linter lo verifica sobre el
grafo de imports real — una deriva arquitectonica = test roto."""
import os
import shutil
import subprocess
import unittest

from tests import ROOT


def _lint_imports():
    cand = os.path.join(ROOT, '.venv', 'Scripts', 'lint-imports.exe')
    return cand if os.path.exists(cand) else shutil.which('lint-imports')


class TestArquitectura(unittest.TestCase):
    @unittest.skipUnless(_lint_imports(),
                         'import-linter no instalado (requirements-dev.txt)')
    def test_capas_no_violadas(self):
        env = dict(os.environ, PYTHONIOENCODING='utf-8',
                   PYTHONPATH=os.path.join(ROOT, 'src'))
        r = subprocess.run([_lint_imports()], capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           cwd=ROOT, timeout=120, env=env)
        self.assertEqual(r.returncode, 0,
                         (r.stdout + r.stderr)[-3000:])
        self.assertIn('KEPT', r.stdout)


if __name__ == '__main__':
    unittest.main()
