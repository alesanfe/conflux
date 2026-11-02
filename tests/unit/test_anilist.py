# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.anilist — row parsing."""
import unittest

import conflux.infra.anilist as A


class TestAnilistRow(unittest.TestCase):
    def test_row_sin_mal_none(self):
        self.assertIsNone(A._anilist_row({'media': {'idMal': None}}))

    def test_row_ok(self):
        a = {'episode': 5, 'airingAt': 100,
             'media': {'idMal': 123, 'coverImage': {'large': 'u'},
                        'title': {'english': 'X'}}}
        r = A._anilist_row(a)
        self.assertEqual(r['ep'], 5)
        self.assertEqual(r['idMal'], 123)
        self.assertEqual(r['title'], 'X')


if __name__ == '__main__':
    unittest.main()
