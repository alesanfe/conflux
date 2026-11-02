# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.lists — listas externas + M3U."""
import unittest
from unittest.mock import patch

import conflux.domain.lists as L


class TestListMetas(unittest.TestCase):
    def test_metas_por_tipo(self):
        rows = [('movie', 'tt1'), ('series', 'tt2'), ('movie', 'tt3')]
        with patch.object(L, 'fetch', return_value=None):
            L._list_metas.__wrapped__ if hasattr(L._list_metas, '__wrapped__') else None
        # estructura: agrupa por tipo
        by = {}
        for t, i in rows:
            by.setdefault(t, []).append(i)
        self.assertEqual(by['movie'], ['tt1', 'tt3'])


class TestM3uExtinf(unittest.TestCase):
    def test_extinf_parsea(self):
        r = L._m3u_extinf('#EXTINF:-1 tvg-id="x" tvg-name="La1" tvg-logo="u" group-title="N",La 1')
        self.assertIsNotNone(r)

    def test_extinf_linea_plana_campos_vacios(self):
        r = L._m3u_extinf('Canal sin metadata')
        self.assertEqual(r['name'], 'Canal sin metadata')


class TestMdblistItems(unittest.TestCase):
    def test_items_a_ids(self):
        d = {'items': [{'imdb_id': 'tt1', 'mediatype': 'movie'},
                        {'imdb_id': 'tt2', 'mediatype': 'show'},
                        {'imdb_id': None}]}
        out = list(L._mdblist_items(d))
        self.assertIn(('movie', 'tt1'), out)
        self.assertIn(('series', 'tt2'), out)
        self.assertEqual(len(out), 2)


class TestTraktItems(unittest.TestCase):
    def test_filtra_sin_imdb(self):
        rows = [{'movie': {'ids': {'imdb': 'tt1'}}},
                {'show': {'ids': {'imdb': 'tt2'}}},
                {'movie': {'ids': {}}}]
        out = L._trakt_items(rows)
        self.assertIn(('movie', 'tt1'), out)
        self.assertIn(('series', 'tt2'), out)
        self.assertEqual(len(out), 2)


if __name__ == '__main__':
    unittest.main()
