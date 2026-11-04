# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Property-based tests (hypothesis): parsers de texto libre upstream."""
import unittest

from hypothesis import given
from hypothesis import strategies as st

import conflux.domain.text as T
from tests.property._strategies import _MAX


class TestParse(unittest.TestCase):
    @_MAX
    @given(st.text())
    def test_parse_size_none_o_positivo(self, t):
        r = T.parse_size(t)
        self.assertTrue(r is None or r >= 0)

    @_MAX
    @given(st.text())
    def test_parse_seeds_none_o_positivo(self, t):
        r = T.parse_seeds(t)
        self.assertTrue(r is None or r >= 0)

    @_MAX
    @given(st.text())
    def test_extract_fields_siempre_dict(self, t):
        f = T.extract_fields({'title': t})
        self.assertIsInstance(f, dict)
        self.assertIn('res', f)

    @_MAX
    @given(st.text())
    def test_spam_siempre_bool(self, t):
        self.assertIsInstance(T._spam({'title': t}), bool)


if __name__ == '__main__':
    unittest.main()
