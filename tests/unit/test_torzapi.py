# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.torzapi — API Torznab/Newznab para *arr."""
import base64
import unittest
from unittest import mock

import conflux.web.torzapi as T
from tests import cfg_swap


def _row(ih='a' * 40, seeds=10, size=2e9, url='', title='Movie.2020.1080p'):
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
