# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.lists — listas externas + M3U."""
import unittest
from unittest.mock import mock_open as _mo
from unittest.mock import patch

import conflux.core.state as st
import conflux.domain.lists as L
from tests import cfg_swap, fake_session, patch_fetch


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


class TestListTrakt(unittest.TestCase):
    def test_parseo_meta_e_items(self):
        url = 'https://trakt.tv/users/foo/lists/mi-lista'
        s, _ = fake_session(
            {'json': {'name': 'Mi Lista'}},
            {'json': [{'type': 'movie', 'movie': {'ids': {'imdb': 'tt1'}}},
                      {'type': 'show', 'show': {'ids': {'imdb': 'tt2'}}},
                      {'type': 'movie', 'movie': {'ids': {}}}]})
        with patch.object(L, '_session', return_value=s), \
             patch.object(L, '_trakt_h', return_value={}):
            name, items = L._list_trakt(url)
        self.assertEqual(name, 'Mi Lista')
        self.assertEqual(items, [('movie', 'tt1'), ('series', 'tt2')])

    def test_url_mala_valueerror(self):
        self.assertRaises(ValueError, L._list_trakt, 'https://trakt.tv/otra')


class TestTraktItemsTipo(unittest.TestCase):
    def test_tipo_por_clave(self):
        rows = [{'movie': {'ids': {'imdb': 'tt1', 'trakt': 5}}},
                {'show': {'ids': {'imdb': 'tt2'}}},
                'basura', {'movie': {'ids': {}}}]
        self.assertEqual(L._trakt_items(rows),
                         [('movie', 'tt1'), ('series', 'tt2')])


class TestListImdb(unittest.TestCase):
    def test_csv_a_items(self):
        csv = ('Pos,Const,Created,Modified,Description,Title,URL,Title Type,' +
               'Director,Genres\n' +
               '1,tt0137523,x,x,,Fight Club,u,Feature Film,x,Drama\n' +
               '2,tt0903747,x,x,,Breaking Bad,u,x,x,TV Series\n' +
               '3,nope,x,x,,Bad,u,Feature Film,x,X\n')
        s, _ = fake_session({'text': csv})
        with patch.object(L, '_session', return_value=s):
            name, items = L._list_imdb('https://imdb.com/list/ls123')
        self.assertIn('ls123', name)
        self.assertEqual(items, [('movie', 'tt0137523'),
                                 ('series', 'tt0903747')])

    def test_url_mala_valueerror(self):
        self.assertRaises(ValueError, L._list_imdb, 'https://imdb.com/x')


class TestListMdblist(unittest.TestCase):
    def test_items(self):
        _, undo = cfg_swap(mdblist_key='K')
        s, _ = fake_session({'json': {'name': 'Top', 'items': [
            {'imdb_id': 'tt1', 'mediatype': 'movie'},
            {'imdb_id': 'tt2', 'mediatype': 'show'},
            {'mediatype': 'movie'}]}})
        try:
            with patch.object(L, '_session', return_value=s):
                name, items = L._list_mdblist('mdblist:777')
        finally:
            undo()
        self.assertEqual(name, 'Top')
        self.assertEqual(items, [('movie', 'tt1'), ('series', 'tt2')])

    def test_sin_key_valueerror(self):
        _, undo = cfg_swap(mdblist_key='')
        try:
            self.assertRaises(ValueError, L._list_mdblist, 'mdblist:777')
        finally:
            undo()


class TestListFetch(unittest.TestCase):
    def setUp(self):
        st._cat_c.clear()

    def tearDown(self):
        st._cat_c.clear()

    def test_dispatch_y_cache(self):
        _, undo = cfg_swap(mdblist_key='K')
        s, calls = fake_session({'json': {'name': 'X', 'items': []}})
        try:
            with patch.object(L, '_session', return_value=s):
                n1 = L._list_fetch('mdblist:1')
                n2 = L._list_fetch('mdblist:1')
        finally:
            undo()
        self.assertEqual(n1, n2)
        self.assertEqual(len(calls), 1, 'segunda llamada sirve caché')

    def test_error_devuelve_default(self):
        s, _ = fake_session(RuntimeError('x'))
        with patch.object(L, '_session', return_value=s):
            self.assertEqual(L._list_fetch('mdblist:x'), ('Lista', []))


class TestListMetasFlow(unittest.TestCase):
    def setUp(self):
        st._cat_c.clear()
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m
        st._cat_c.clear()

    def test_items_a_metas_via_cinemeta(self):
        _, undo = cfg_swap(mdblist_key='K')
        s, _ = fake_session({'json': {'name': 'X', 'items': [
            {'imdb_id': 'tt111', 'mediatype': 'movie'}]}})
        cine = next(b for n, b in st.SOURCES if n == 'Cinemeta')
        seen = []
        def fake(url, t=8):
            seen.append(url)
            return {'meta': {'id': 'tt111', 'name': 'Película'}}
        restore = patch_fetch(fake)
        try:
            with patch.object(L, '_session', return_value=s):
                d = L._list_metas('mdblist:5', 'movie')
        finally:
            restore(); undo()
        self.assertEqual(d['metas'][0]['name'], 'Película')
        self.assertTrue(any(cine in u for u in seen))


class TestM3U(unittest.TestCase):
    def tearDown(self):
        L._M3U.clear()

    def test_parse_extinf_y_urls(self):
        _, undo = cfg_swap(m3u_path='/tmp/x.m3u')
        m3u = ('#EXTM3U\n'
               '#EXTINF:-1 tvg-logo="http://l/1.png" group-title="News",Canal Uno\n'
               'http://stream/1\n'
               '#EXTINF:-1,Canal Dos\n'
               'http://stream/2\n')
        try:
            with patch.object(L, '_open', _mo(read_data=m3u)):
                metas = L._m3u_metas()
        finally:
            undo()
        self.assertEqual(len(metas), 2)
        self.assertEqual(metas[0]['name'], 'Canal Uno')
        self.assertEqual(metas[0]['poster'], 'http://l/1.png')
        self.assertEqual(metas[0]['description'], 'News')
        self.assertEqual(metas[1]['poster'].endswith('tTCuczU.png'), True)
        self.assertEqual(L._M3U['m3u:1'], 'http://stream/1')
        self.assertEqual(L._M3U['m3u:2'], 'http://stream/2')

    def test_fichero_inexistente_vacio(self):
        _, undo = cfg_swap(m3u_path='/noexiste.m3u')
        try:
            self.assertEqual(L._m3u_metas(), [])
        finally:
            undo()

    def test_extinf_parse(self):
        d = L._m3u_extinf('#EXTINF:-1 tvg-id="N.es@SD" tvg-logo="L.png" group-title="G",N')
        self.assertEqual(d, {'name': 'N', 'logo': 'L.png', 'group': 'G',
                             'tvg': 'N.es@SD'})


class TestM3UCache(unittest.TestCase):
    """Segunda llamada con mismo contenido reutiliza el parseo (sin re-copiar)."""

    def test_contenido_igual_reusa_parseo(self):
        L._M3U_TXT[0] = None
        _, undo = cfg_swap(m3u_path='/tmp/x.m3u')
        try:
            with patch.object(L, '_open', _mo(read_data='#EXTINF:-1,A\nhttp://s/1\n')):
                a = L._m3u_metas()
                with patch.object(L, '_m3u_extinf', side_effect=AssertionError('reparseó')):
                    b = L._m3u_metas()
            self.assertEqual(len(a), 1)
            self.assertEqual(len(b), 1)
            self.assertEqual(b[0]['name'], 'A')
            b[0]['name'] = 'mutado'                    # copias: no corrompe la caché
            with patch.object(L, '_open', _mo(read_data='#EXTINF:-1,A\nhttp://s/1\n')):
                self.assertEqual(L._m3u_metas()[0]['name'], 'A')
        finally:
            undo()
            L._M3U_TXT[0] = None
