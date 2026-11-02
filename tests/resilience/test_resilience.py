# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Resilience tests: upstreams que devuelven basura no deben romper el proxy.

R5: transitorio/malformado ≠ roto — la respuesta al cliente sigue siendo un
payload Stremio válido (vacío/stale), nunca una excepción o 500."""
import unittest

import conflux.app.meta as M
import conflux.app.stream as S
import conflux.app.subs as SUB
import conflux.core.state as st
import conflux.domain.catalog as C
from tests import patch_fetch

# Respuestas upstream degeneradas que se han visto o son plausibles:
MALFORMED = [
    ('json_no_dict', [1, 2, 3]),
    ('json_str', 'not-json'),
    ('json_int', 42),
    ('streams_str', {'streams': 'broken'}),
    ('streams_tipos_mal', {'streams': [None, 42, 'x', {'infoHash': 123}]}),
    ('meta_lista', {'meta': []}),
    ('metas_str', {'metas': 'x'}),
    ('metas_nulls', {'metas': [None, None]}),
    ('dict_vacio', {}),
    ('null', None),
]


class TestMalformedUpstream(unittest.TestCase):
    """Cada handler tolera toda la matriz de respuestas degeneradas."""

    def setUp(self):
        S._cache.clear(); S._inflight.clear(); C._cat_c.clear(); st._meta_c.clear()
        self._m = list(st.manifests)
        st.manifests[:] = [{'resources': [
            {'name': 'stream', 'types': ['movie', 'series'], 'idPrefixes': ['tt']},
            {'name': 'meta', 'types': ['movie', 'series']}]}]

    def tearDown(self):
        st.manifests[:] = self._m
        S._cache.clear(); S._inflight.clear(); C._cat_c.clear(); st._meta_c.clear()

    def test_handle_stream_matriz(self):
        for name, payload in MALFORMED:
            with self.subTest(payload=name):
                S._cache.clear()
                restore = patch_fetch(lambda *a, _p=payload, **k: _p)
                try:
                    d = S.handle_stream('movie', 'tt1')
                finally:
                    restore()
                self.assertIsInstance(d.get('streams'), list)
                for s in d['streams']:
                    self.assertIsInstance(s, dict)

    def test_handle_catalog_matriz(self):
        for name, payload in MALFORMED:
            with self.subTest(payload=name):
                C._cat_c.clear()
                restore = patch_fetch(lambda *a, _p=payload, **k: _p)
                try:
                    d = C.handle_catalog('movie', 'scinemeta_movie_top', None)
                finally:
                    restore()
                self.assertIsInstance(d, dict)
                if 'metas' in d:
                    self.assertIsInstance(d['metas'], list)

    def test_handle_meta_matriz(self):
        for name, payload in MALFORMED:
            with self.subTest(payload=name):
                st._meta_c.clear()
                restore = patch_fetch(lambda *a, _p=payload, **k: _p)
                try:
                    d = M.handle_meta('movie', 'tt1')
                finally:
                    restore()
                self.assertIsInstance(d.get('meta'), dict)

    def test_handle_subtitles_matriz(self):
        for name, payload in MALFORMED:
            with self.subTest(payload=name):
                restore = patch_fetch(lambda *a, _p=payload, **k: _p)
                try:
                    d = SUB.handle_subtitles('movie', 'tt1')
                finally:
                    restore()
                self.assertIsInstance(d.get('subtitles'), list)

    def test_fetch_que_lanza_no_mata_handler(self):
        """fetch diseñado para tragar errores; si aún así lanza, no debe
        arrastrar el handler entero."""
        def boom(*a, **k):
            raise RuntimeError('upstream caído')
        restore = patch_fetch(boom)
        try:
            d = S.handle_stream('movie', 'tt1')
            self.assertIsInstance(d.get('streams'), list)
        finally:
            restore()
