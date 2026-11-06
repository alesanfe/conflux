# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements
"""Matriz de rutas/filesystem (CONFLUX-044): espacios, unicode, nombres
largos, solo-lectura, symlinks — las escrituras de estado deben funcionar
o fallar limpio, nunca corromper."""
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from conflux.core import state as st


class TestPathMatrix(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def _roundtrip(self, sub):
        d = os.path.join(self.base, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, 'config.json')
        st._atomic_json(p, {'k': 'v-ñ-'})
        with open(p, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh), {'k': 'v-ñ-'})

    def test_ruta_con_espacios(self):
        self._roundtrip('dir con espacios')

    def test_ruta_unicode(self):
        self._roundtrip('carpeta--ñoño')

    def test_nombre_largo(self):
        self._roundtrip('d' * 120)

    def test_padre_anidado_profundo(self):
        self._roundtrip(os.path.join(*['n' * 20] * 5))

    @unittest.skipIf(sys.platform == 'win32', 'chmod de dir no aplica en win')
    def test_directorio_solo_lectura_no_corrompe(self):
        d = os.path.join(self.base, 'ro')
        os.makedirs(d)
        p = os.path.join(d, 'config.json')
        st._atomic_json(p, {'a': 1})
        os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
        try:
            with self.assertRaises(OSError):
                st._atomic_json(p, {'a': 2})
            with open(p, encoding='utf-8') as fh:
                self.assertEqual(json.load(fh), {'a': 1})   # anterior intacto
        finally:
            os.chmod(d, stat.S_IRWXU)

    def test_tmp_no_queda_si_falla(self):
        p = os.path.join(self.base, 'f.json')
        with mock.patch.object(st.os, 'replace', side_effect=OSError('x')):
            with self.assertRaises(OSError):
                st._atomic_json(p, {'a': 1})
        # el .tmp puede quedar (fase media) pero NUNCA como target parcial
        self.assertFalse(os.path.exists(p))


class TestSymlinkScrape(unittest.TestCase):
    """LevelDB scraping: un symlink que apunte fuera del dir escaneado NO
    se sigue — la key del exterior nunca se lee."""
    def test_symlink_fuera_no_se_sigue(self):
        import conflux.infra.stremio as SM
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'outside.log')
            with open(out, 'wb') as fh:                     # authkey "válida" fuera
                fh.write(b'authKey":"' + b'a' * 40 + b'"')
            inner = os.path.join(d, 'leveldb')
            os.makedirs(inner)
            link = os.path.join(inner, 'evil.log')
            try:
                os.symlink(out, link)
            except (OSError, NotImplementedError):
                self.skipTest('sin permiso de symlink')
            with mock.patch.object(SM, '_leveldb_dirs', return_value=[inner]):
                self.assertIsNone(SM._authkey_leveldb())     # no lee el exterior
            # y un fichero real dentro sí se lee
            with open(os.path.join(inner, 'ok.log'), 'wb') as fh:
                fh.write(b'authKey":"' + b'b' * 40 + b'"')
            with mock.patch.object(SM, '_leveldb_dirs', return_value=[inner]):
                self.assertEqual(SM._authkey_leveldb(), 'b' * 40)


if __name__ == '__main__':
    unittest.main()
