# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Contract tests: las respuestas cumplen el contrato del protocolo Stremio.

El manifest es contrato cliente-servidor (R3): Stremio filtra del lado cliente
ANTES de pedir nada — un campo mal formado significa 'No addons were requested'
sin tocar el proxy. Estos tests validan la FORMA de cada payload, no los datos."""
import unittest

import conflux.app.meta as M
import conflux.app.stream as S
import conflux.app.subs as SUB
import conflux.core.state as st
import conflux.domain.catalog as C
import conflux.domain.manifest as MF
from tests import patch_fetch

STREMIO_TYPES = {'movie', 'series', 'channel', 'tv', 'anime', 'other'}
STREAM_SOURCE_KEYS = ('url', 'infoHash', 'externalUrl', 'ytId', 'playerFrameUrl')


def assert_manifest(test, m):
    """Contrato del addon manifest (lo que Stremio parsea al instalar)."""
    for k in ('id', 'version', 'name', 'description'):
        test.assertIsInstance(m.get(k), str, f'manifest.{k} debe ser str')
        test.assertTrue(m[k], f'manifest.{k} vacío')
    test.assertIsInstance(m.get('resources'), list)
    for r in m['resources']:
        if isinstance(r, dict):
            test.assertIsInstance(r.get('name'), str)
        else:
            test.assertIsInstance(r, str)
    for t in m.get('types', []):
        test.assertIn(t, STREMIO_TYPES, f'tipo no stremio: {t}')
    test.assertIsInstance(m.get('idPrefixes'), list)
    test.assertTrue(m['idPrefixes'], 'sin idPrefixes el cliente nunca pide nada')
    for c in m.get('catalogs', []):
        test.assertIsInstance(c.get('type'), str)
        test.assertIsInstance(c.get('id'), str)
        test.assertIsInstance(c.get('name'), str)
        for e in c.get('extra', []):
            test.assertIsInstance(e.get('name'), str)
            if 'isRequired' in e:
                test.assertIsInstance(e['isRequired'], bool)
            if 'options' in e:
                test.assertIsInstance(e['options'], list)


def assert_stream(test, s):
    """Contrato de un objeto stream (stremio-web es frágil con tipos)."""
    for k, v in s.items():
        test.assertIsNotNone(v, f'stream.{k} es null — rompe el cliente')
    srcs = [k for k in STREAM_SOURCE_KEYS if k in s]
    test.assertTrue(srcs, 'stream sin fuente (url/infoHash/externalUrl/ytId)')
    if 'infoHash' in s:
        test.assertIsInstance(s['infoHash'], str)
        test.assertEqual(s['infoHash'], s['infoHash'].lower())
        test.assertTrue(s['infoHash'], 'infoHash vacío')
    if 'fileIdx' in s:
        test.assertIsInstance(s['fileIdx'], int)
        test.assertNotIsInstance(s['fileIdx'], bool)
    if 'name' in s:
        test.assertIsInstance(s['name'], str)
    if 'title' in s:
        test.assertIsInstance(s['title'], str)
    if 'behaviorHints' in s:
        test.assertIsInstance(s['behaviorHints'], dict)


def assert_meta(test, meta):
    """Contrato de un objeto meta."""
    test.assertIsInstance(meta.get('id'), str)
    test.assertIsInstance(meta.get('type'), str)
    test.assertIsInstance(meta.get('name'), str)
    if 'videos' in meta:
        test.assertIsInstance(meta['videos'], list)
        for v in meta['videos']:
            test.assertIsInstance(v, dict)
            test.assertIsInstance(v.get('id'), str)


def assert_catalog_resp(test, d):
    test.assertIsInstance(d, dict)
    key = 'metasDetailed' if 'metasDetailed' in d else 'metas'
    test.assertIn(key, d)
    test.assertIsInstance(d[key], list)
    for m in d[key]:
        if m is not None:
            test.assertIsInstance(m.get('id'), str)


def assert_subtitles_resp(test, d):
    test.assertIsInstance(d.get('subtitles'), list)
    for s in d['subtitles']:
        test.assertIsInstance(s.get('id'), str)
        test.assertIsInstance(s.get('url'), str)
        test.assertIsInstance(s.get('lang'), str)


class TestManifestContract(unittest.TestCase):
    def setUp(self):
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m

    def test_manifest_cumple_contrato(self):
        st.manifests[:] = [{
            'resources': [{'name': 'catalog', 'types': ['movie']},
                          {'name': 'stream', 'types': ['movie'], 'idPrefixes': ['tt']}],
            'catalogs': [{'id': 'top', 'type': 'movie', 'name': 'Top',
                          'extra': [{'name': 'genre', 'options': ['2025']}]}],
        }]
        restore = patch_fetch(lambda *a, **k: None)
        try:
            m = MF.build_manifest()
        finally:
            restore()
        assert_manifest(self, m)

    def test_catalog_ids_unicos(self):
        """cids duplicados = catálogos que se pisan en Discover."""
        st.manifests[:] = []
        restore = patch_fetch(lambda *a, **k: None)
        try:
            m = MF.build_manifest()
        finally:
            restore()
        cids = [c['id'] for c in m['catalogs']]
        self.assertEqual(len(cids), len(set(cids)), 'cids duplicados')


class TestStreamContract(unittest.TestCase):
    def setUp(self):
        S._cache.clear(); S._inflight.clear()
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m
        S._cache.clear(); S._inflight.clear()

    def test_streams_cumplen_contrato(self):
        st.manifests[:] = [{'resources': [
            {'name': 'stream', 'types': ['movie'], 'idPrefixes': ['tt']}]}]
        up = {'streams': [
            {'infoHash': 'AA' * 20, 'title': 'X 1080p 👤10 💾1 GB', 'fileIdx': '2'},
            {'url': 'https://x/v.mp4', 'title': 'Y 720p'},
            {'infoHash': 'b' * 40, 'title': None, 'fileIdx': None}]}
        restore = patch_fetch(lambda *a, **k: up)
        try:
            d = S.handle_stream('movie', 'tt0000001')
        finally:
            restore()
        self.assertIsInstance(d['streams'], list)
        self.assertTrue(d['streams'])
        for s in d['streams']:
            assert_stream(self, s)


class TestMetaContract(unittest.TestCase):
    def setUp(self):
        st._meta_c.clear()
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m
        st._meta_c.clear()

    def test_meta_cumple_contrato(self):
        st.manifests[:] = [{'resources': [{'name': 'meta', 'types': ['movie']}]}]
        up = {'meta': {'id': 'tt1', 'type': 'movie', 'name': 'X',
                       'videos': [{'id': 'tt1:1:1', 'season': 1, 'episode': 1}]}}
        restore = patch_fetch(lambda *a, **k: up)
        try:
            d = M.handle_meta('movie', 'tt1')
        finally:
            restore()
        self.assertIsInstance(d, dict)
        assert_meta(self, d['meta'])

    def test_meta_vacia_es_dict_no_null(self):
        st.manifests[:] = []
        restore = patch_fetch(lambda *a, **k: None)
        try:
            d = M.handle_meta('movie', 'tt_inexistente')
        finally:
            restore()
        self.assertIsInstance(d['meta'], dict)


class TestCatalogContract(unittest.TestCase):
    def setUp(self):
        C._cat_c.clear()

    def tearDown(self):
        C._cat_c.clear()

    def test_catalog_cumple_contrato(self):
        up = {'metas': [{'id': 'tt1', 'type': 'movie', 'name': 'X'}]}
        restore = patch_fetch(lambda *a, **k: up)
        try:
            d = C.handle_catalog('movie', 'scinemeta_movie_top', None)
        finally:
            restore()
        assert_catalog_resp(self, d)

    def test_subtitles_cumple_contrato(self):
        up = {'subtitles': [{'id': 'x1', 'url': 'https://x/s.srt', 'lang': 'spa'}]}
        restore = patch_fetch(lambda *a, **k: up)
        try:
            d = SUB.handle_subtitles('movie', 'tt1')
        finally:
            restore()
        assert_subtitles_resp(self, d)
