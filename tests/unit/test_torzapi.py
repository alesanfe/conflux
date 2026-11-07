# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.torzapi — API Torznab/Newznab para *arr."""
import base64
import re
import unittest
from unittest import mock
from xml.sax.saxutils import escape

import conflux.web.torzapi as T
from tests import cfg_swap


def _row(ih='a' * 40, seeds=10, size=2.0, url='', title='Movie.2020.1080p'):
    out = {'title': title, 'infoHash': ih}
    if url:
        out['url'] = url
        out.pop('infoHash', None)
    return (out, '1080P', seeds, size, 0, {'title': title}, 'Src')


class TestAuth(unittest.TestCase):
    def test_abierto_sin_claves(self):
        _, undo = cfg_swap(torzapi_key='', admin_token='')
        try:
            self.assertTrue(T._torz_auth({}))
        finally:
            undo()

    def test_apikey_requerida(self):
        _, undo = cfg_swap(torzapi_key='secret', admin_token='')
        try:
            self.assertFalse(T._torz_auth({}))
            self.assertFalse(T._torz_auth({'apikey': ['bad']}))
            self.assertTrue(T._torz_auth({'apikey': ['secret']}))
        finally:
            undo()

    def test_fallback_admin_token(self):
        _, undo = cfg_swap(torzapi_key='', admin_token='adm')
        try:
            self.assertTrue(T._torz_auth({'apikey': ['adm']}))
        finally:
            undo()


class TestCaps(unittest.TestCase):
    def test_caps_xml(self):
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body = T.handle_torzapi({'t': ['caps'], 'apikey': ['k']})
        finally:
            undo()
        self.assertEqual(code, 200)
        self.assertIn('<caps>', body)
        self.assertIn('tv-search', body)
        self.assertIn('movie-search', body)

    def test_sin_auth_401(self):
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body = T.handle_torzapi({'t': ['caps']})
        finally:
            undo()
        self.assertEqual(code, 401)
        self.assertIn('Invalid API Key', body)
        self.assertIn('code="100"', body)          # código Newznab (100+1)


class TestSearch(unittest.TestCase):
    def _run(self, q, rows=()):
        state = {'done': _Evt(), 'lock': __import__('threading').Lock(),
                 'rows': list(rows)}
        with mock.patch.object(T, '_ensure_collect', return_value=state) as ec:
            code, body = T.handle_torzapi(q)
        return code, body, ec

    def test_sin_imdb_vacio(self):
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run({'t': ['movie'], 'apikey': ['k'], 'q': ['x']})
        finally:
            undo()
        self.assertEqual(code, 200)
        self.assertIn('<channel>', body)
        self.assertNotIn('<item>', body)          # q sin imdb: feed vacío

    def test_movie_items(self):
        _, undo = cfg_swap(torzapi_key='k', sources_disabled=[], source_groups={})
        try:
            code, body, ec = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[_row()])
        finally:
            undo()
        self.assertEqual(code, 200)
        ec.assert_called_once()
        key = ec.call_args[0][0]
        self.assertEqual(key[0], 'movie')
        self.assertEqual(key[1], 'tt0137523')
        self.assertIn('magnet:?xt=urn:btih:' + 'a' * 40, body)
        self.assertIn('application/x-bittorrent', body)
        self.assertIn('name="seeders" value="10"', body)
        self.assertIn('name="category" value="2000"', body)

    def test_tvsearch_sid_compuesto(self):
        _, undo = cfg_swap(torzapi_key='k')
        try:
            _, _, ec = self._run({'t': ['tvsearch'], 'apikey': ['k'],
                                  'imdbid': ['tt0944947'], 'season': ['01'], 'ep': ['2']})
        finally:
            undo()
        self.assertEqual(ec.call_args[0][0][1], 'tt0944947:1:2')

    def test_title_fallback_out(self):
        """f dict sin title -> usa out.title (Or@78: `or`->`and` daría
        'release' siempre con f.title falsy)."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            row = ({'title': 'OutT', 'infoHash': 'a' * 40},
                   '1080P', 1, 1e9, 0, {'otra': 'x'}, 'S')
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[row])
        finally:
            undo()
        self.assertIn('<title>OutT</title>', body)

    def test_title_cap_300(self):
        """title[:300] (300+1@79) y `or 'release'` (Or@79: `and` emitiría
        'release' con título truthy)."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            row = ({'title': 'X' * 400, 'infoHash': 'a' * 40},
                   '1080P', 1, 1e9, 0, {'title': 'X' * 400}, 'S')
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[row])
        finally:
            undo()
        self.assertIn('X' * 300, body)
        self.assertNotIn('X' * 301, body)
        self.assertNotIn('<title>release</title>', body)

    def test_infohash_no_str_sin_magnet(self):
        """isinstance(ih,str) and ih (And@81: `or` emitiría magnet con
        infoHash no-string)."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            row = ({'title': 'T', 'infoHash': 12345},
                   '1080P', 1, 1e9, 0, {'title': 'T'}, 'S')
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[row])
        finally:
            undo()
        self.assertNotIn('magnet:?xt=urn:btih:12345', body)

    def test_guid_cap_120(self):
        """guid = u[:120] (120+1@100)."""
        url = 'https://nzb.dl/' + 'a' * 200
        b64 = base64.urlsafe_b64encode(url.encode()).decode().rstrip('=')
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[_row(url=f'http://h/nzb/{b64}')])
        finally:
            undo()
        m = re.search(r'<guid[^>]*>([^<]+)</guid>', body)
        self.assertIsNotNone(m)
        self.assertEqual(len(m.group(1)), len(escape(url[:120])))

    def test_cat_5000_tvsearch(self):
        """cat=5000 para series, 2000 para movies (5000+1/2000+1@116)."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run(
                {'t': ['tvsearch'], 'apikey': ['k'], 'imdbid': ['tt0944947']},
                rows=[_row()])
        finally:
            undo()
        self.assertIn('name="category" value="5000"', body)

    def test_tipo_desconocido_feed_vacio(self):
        """stype None -> feed vacío sin collect (Or@118: `and` seguiría
        adelante con sid=imdb)."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, ec = self._run(
                {'t': ['xxx'], 'apikey': ['k'], 'imdbid': ['tt0137523']})
        finally:
            undo()
        self.assertEqual(code, 200)
        self.assertNotIn('<item>', body)
        ec.assert_not_called()

    def test_season_no_digit_sin_ep(self):
        """se/ep isdigit AMBOS requeridos (And@124: `or` haría int('abc')
        -> crash, o sid con ep cuando solo una es válida)."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            _, _, ec = self._run({'t': ['tvsearch'], 'apikey': ['k'],
                                  'imdbid': ['tt0944947'], 'season': ['abc'],
                                  'ep': ['1']})
            _, _, ec2 = self._run({'t': ['tvsearch'], 'apikey': ['k'],
                                   'imdbid': ['tt0944947'], 'season': ['1'],
                                   'ep': ['']})
        finally:
            undo()
        self.assertEqual(ec.call_args[0][0][1], 'tt0944947')
        self.assertEqual(ec2.call_args[0][0][1], 'tt0944947')

    def test_orden_por_seeds_y_cap_200(self):
        """sorted por r[2] desc (Gt/2+1@130) y cap [:200] (200+1@130)."""
        rows = [_row(ih=f'{i:040x}', seeds=i, title=f'T{i}') for i in range(201)]
        rows.append(('solo2', 'x'))                        # len 2 -> seeds 0
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=rows)
        finally:
            undo()
        self.assertEqual(code, 200)
        items = re.findall(r'<item>', body)
        self.assertEqual(len(items), 200)                  # [:200] cap
        first = body.index('name="seeders" value="200"')   # seeds desc
        self.assertLess(first, body.index('name="seeders" value="199"'))

    def test_cat_2000_movie(self):
        """cat=2000 en la rama movie (2000+1@116: el literal movie->2001)."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[_row()])
        finally:
            undo()
        self.assertIn('name="category" value="2000"', body)

    def test_fila_len3_orden_seeds(self):
        """len(r)>2 -> seeds=r[2] (2+1@130 -> >3: la fila corta valdría 0,
        quedaría fuera del cap y saldrían los 200 items válidos)."""
        rows = [_row(ih=f'{i:040x}', seeds=i + 1, title=f'T{i}')
                for i in range(200)]
        rows.insert(0, ('corta', 'x', 999))          # len-3, seeds 999 -> 1ª
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=rows)
        finally:
            undo()
        # la corta gana un slot del top-200 pero no emite item -> 199
        self.assertEqual(body.count('<item>'), 199)

    def test_size_en_bytes(self):
        """La fila lleva GB (GiB float); newznab size/enclosure van en bytes —
        int(size) a pelo emitía '1' (1 byte) para un release de 2GB."""
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[_row(size=2.0)])
        finally:
            undo()
        gb = int(2.0 * (1 << 30))
        self.assertIn(f'name="size" value="{gb}"', body)
        self.assertIn(f'length="{gb}"', body)
        self.assertNotIn('value="2"', body)

    def test_item_usenet(self):
        nzb = 'https://idx/get/x.nzb'
        b64 = base64.urlsafe_b64encode(nzb.encode()).decode().rstrip('=')
        _, undo = cfg_swap(torzapi_key='k')
        try:
            code, body, _ = self._run(
                {'t': ['movie'], 'apikey': ['k'], 'imdbid': ['tt0137523']},
                rows=[_row(url=f'http://127.0.0.1:9700/nzb/{b64}')])
        finally:
            undo()
        self.assertIn('application/x-nzb', body)
        self.assertIn('https://idx/get/x.nzb', body)


class TestNzbLink(unittest.TestCase):
    def test_padding_b64_len15(self):
        """'=' * (-len(b) % 4): len(b)=15 -> 1 pad (4+1@66 -> %5 daría 0:
        15 chars no múltiplo de 4 -> decode error -> '')."""
        nzb = 'https://a.b'                       # 11B -> b64 15 chars
        b64 = base64.urlsafe_b64encode(nzb.encode()).decode().rstrip('=')
        self.assertEqual(len(b64) % 4, 3)
        self.assertEqual(T._nzb_link({'url': f'http://h/nzb/{b64}'}), nzb)

    def test_nzburl_directo(self):
        self.assertEqual(T._nzb_link({'nzbUrl': 'https://x/y.nzb'}), 'https://x/y.nzb')

    def test_b64_interno(self):
        nzb = 'https://nz/x.nzb'
        b64 = base64.urlsafe_b64encode(nzb.encode()).decode().rstrip('=')
        self.assertEqual(T._nzb_link({'url': f'http://h/nzb/{b64}'}), nzb)

    def test_nada(self):
        self.assertEqual(T._nzb_link({'url': 'http://x/stream'}), '')


class _Evt:
    def __init__(self):
        self._e = True
    def wait(self, _t=None):
        return True


if __name__ == '__main__':
    unittest.main()
