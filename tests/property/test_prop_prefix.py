# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Property-based tests (hypothesis): _id_prefix sobre entrada arbitraria."""
import unittest

from hypothesis import given
from hypothesis import strategies as st

import conflux.domain.text as T
from tests.property._strategies import _MAX


class TestIdPrefix(unittest.TestCase):
    @_MAX
    @given(st.text())
    def test_nunca_explota_siempre_str(self, sid):
        p = T._id_prefix(sid)
        self.assertIsInstance(p, str)

    @_MAX
    @given(st.from_regex(r'[a-zA-Z_]{1,10}[:0-9a-zA-Z._-]{0,20}', fullmatch=True))
    def test_prefijo_letras_iniciales(self, sid):
        """Si empieza por letras/underscore, devuelve exactamente ese run."""
        import re
        p = T._id_prefix(sid)
        run = re.match(r'[a-zA-Z_]+', sid).group()
        self.assertEqual(p, run)


if __name__ == '__main__':
    unittest.main()
