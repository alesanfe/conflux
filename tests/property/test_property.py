# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Property-based tests (hypothesis): invariantes sobre TODO el espacio de entrada.

En vez de ejemplos puntuales, se afirman propiedades y hypothesis genera
cientos de casos buscando contraejemplos — ideal para parsers que consumen
texto arbitrario de upstreams."""
import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import conflux.domain.text as T

_MAX = settings(max_examples=200, deadline=None,
               suppress_health_check=list(HealthCheck))

_ANY_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() |
    st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=50),
    lambda ch: st.lists(ch, max_size=4) |
    st.dictionaries(st.text(max_size=12), ch, max_size=4),
    max_leaves=25)

_STREAM_DICT = st.dictionaries(
    st.sampled_from(['infoHash', 'url', 'fileIdx', 'title', 'name', 'sources',
                     'behaviorHints', 'seeders', 'size', 'description']),
    _ANY_JSON, max_size=8)


class TestIdPrefix(unittest.TestCase):
    @_MAX
    @given(st.text())
    def test_nunca_explota_siempre_str(self, sid):
        p = T._id_prefix(sid)
        self.assertIsInstance(p, str)

    @_MAX
    @given(st.from_regex(r'[a-zA-Z_]{1,10}[:0-9a-zA-Z._-]{0,20}', fullmatch=True))
    def test_prefijo_letras_iniciales(self, sid):
        """Si empieza por letras/underscore, devuelve exactamente ese run."""
        import re
        p = T._id_prefix(sid)
        run = re.match(r'[a-zA-Z_]+', sid).group()
        self.assertEqual(p, run)


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

    @_MAX
    @given(_STREAM_DICT)
    def test_norm_stream_tupla_coherente(self, s):
        out, _res, seeds, size, _cached, _f, _short = T.norm_stream(s, 'x')
        self.assertIsInstance(out, dict)
        self.assertIsInstance(out['name'], str)
        self.assertIsInstance(out['title'], str)
        self.assertGreaterEqual(seeds, 0)
        self.assertGreaterEqual(size, 0)
