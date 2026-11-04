# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Property-based tests (hypothesis): sanitize_stream sobre dicts arbitrarios."""
import unittest

from hypothesis import given

import conflux.domain.text as T
from tests.property._strategies import _MAX, _STREAM_DICT


class TestSanitize(unittest.TestCase):
    @_MAX
    @given(_STREAM_DICT)
    def test_sanitize_invariantes(self, s):
        """Postcondiciones de sanitize_stream para CUALQUIER dict de entrada:
        sin nulls, tipos stremio válidos, fileIdx int real, infoHash minúscula."""
        out = T.sanitize_stream(s)
        self.assertIsInstance(out, dict)
        for v in out.values():
            self.assertIsNotNone(v)
        if 'fileIdx' in out:
            self.assertIsInstance(out['fileIdx'], int)
            self.assertNotIsInstance(out['fileIdx'], bool)
        if 'infoHash' in out:
            self.assertEqual(out['infoHash'], out['infoHash'].lower())
        for k, typ in (('infoHash', str), ('url', str), ('sources', list),
                       ('behaviorHints', dict)):
            if k in out:
                self.assertIsInstance(out[k], typ)


if __name__ == '__main__':
    unittest.main()
