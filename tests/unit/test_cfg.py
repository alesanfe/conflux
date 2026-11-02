# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.core.cfg — config + perfiles."""
import re
import unittest

from conflux.core import cfg


class TestLoadConfig(unittest.TestCase):
    def test_cfgget_default(self):
        self.assertEqual(cfg._cfgget('noexiste_clave_xyz', 42), 42)

    def test_es_pat_regex(self):
        pat = cfg._es_pat()
        self.assertIsInstance(pat, str)
        self.assertTrue(re.search(pat, 'audio es dual'))
        self.assertFalse(re.search(pat, 'español'))  # \b exige palabra completa
        self.assertFalse(re.search(pat, 'english'))

    def test_re_es_existe(self):
        self.assertTrue(cfg.RE_ES.search('es'))
        self.assertFalse(cfg.RE_ES.search('en'))


class TestEffDisabled(unittest.TestCase):
    def test_eff_disabled_lista(self):
        self.assertIsInstance(cfg._eff_disabled(), set)


if __name__ == '__main__':
    unittest.main()
