# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Property-based tests (hypothesis): norm_stream sobre dicts arbitrarios."""
import unittest

from hypothesis import given

import conflux.domain.text as T
from tests.property._strategies import _MAX, _STREAM_DICT


class TestNormStream(unittest.TestCase):
    @_MAX
    @given(_STREAM_DICT)
    def test_norm_stream_tupla_coherente(self, s):
        out, _res, seeds, size, _cached, _f, _short = T.norm_stream(s, 'x')
        self.assertIsInstance(out, dict)
        self.assertIsInstance(out['name'], str)
        self.assertIsInstance(out['title'], str)
        self.assertGreaterEqual(seeds, 0)
        self.assertGreaterEqual(size, 0)


if __name__ == '__main__':
    unittest.main()
