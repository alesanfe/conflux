# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.sources — recursos, resolución de ids."""
import os
import unittest

import conflux.domain.sources as _sources
import conflux.infra.net as _net
from tests import px


class TestInfra(unittest.TestCase):
    def test_res_of_name_o_id(self):
        self.assertIn('stream', px.res_of({'resources': [{'id': 'stream'}]}))
        self.assertIn('stream', px.res_of({'resources': [{'name': 'stream'}]}))
        self.assertIn('stream', px.res_of({'resources': ['stream']}))

    def test_res_types(self):
        m = {'resources': [{'name': 'stream', 'types': ['movie']}]}
        self.assertEqual(px.res_types(m, 'stream'), {'movie'})
        self.assertIsNone(px.res_types(m, 'meta'))

    def test_res_prefixes_no_dict(self):
        """isinstance(r, dict) and ... — entrada no-dict se salta sin .get
        (mutante And@86: 'x'.get -> AttributeError)."""
        from conflux.domain import sources as _s
        self.assertIsNone(_s.res_prefixes({'resources': ['x']}, 'stream'))
        self.assertIsNone(_s.res_prefixes({'resources': [None]}, 'stream'))


class TestResolveSid(unittest.TestCase):
    def setUp(self):
        px._idmap.clear()

    def tearDown(self):
        px._idmap.clear()

    def test_episodio_absoluto_a_SE(self):
        """kitsu:49847:2 (ep absoluto) -> tt...:1:2 vía videos del meta."""
        meta = {'meta': {'imdb_id': 'tt2359704', 'videos': [
            {'id': 'kitsu:49847:1', 'season': '1', 'episode': '1'},
            {'id': 'kitsu:49847:2', 'season': '1', 'episode': '2'}]}}
        orig = _sources.fetch
        _sources.fetch = _net.fetch = lambda *a, **k: meta
        try:
            self.assertEqual(px._resolve_sid('series', 'kitsu:49847:2'), 'tt2359704:1:2')
            self.assertEqual(px._resolve_sid('series', 'kitsu:7442:1:1'), 'tt2359704:1:1')
        finally:
            _sources.fetch = _net.fetch = orig

    def test_tt_no_se_traduce(self):
        self.assertEqual(px._resolve_sid('series', 'tt0944947:1:1'), 'tt0944947:1:1')

    def test_cache_idmap_no_repite_fetch(self):
        meta = {'meta': {'imdb_id': 'tt1', 'videos': []}}
        calls = []
        orig = _sources.fetch
        _sources.fetch = _net.fetch = lambda *a, **k: (calls.append(a), meta)[1]
        try:
            px._resolve_sid('series', 'kitsu:49847:2')
            px._resolve_sid('series', 'kitsu:49847:2')
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], 4)                  # mutante 4+1@104
        finally:
            _sources.fetch = _net.fetch = orig


if __name__ == '__main__':
    unittest.main()


class TestIdMapNoTTL(unittest.TestCase):
    """_idmap no expira: el mapeo prefijo->imdb es estable en el tiempo."""

    def test_entrada_vieja_sigue_resolviendo(self):
        _sources._idmap.clear()
        _sources._idmap[('kitsu', '999')] = (0, ('tt0009999', []))   # ts=0 = prehistórico
        imdb, _ = _sources._resolve_meta('series', 'kitsu', '999')
        self.assertEqual(imdb, 'tt0009999')


class TestAtomicJson(unittest.TestCase):
    """_atomic_json: tmp+rename — el fichero final nunca queda a medias."""

    def test_escribe_y_reemplaza(self):
        import conflux.core.state as st
        import tempfile
        fd, path = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        import json
        st._atomic_json(path, {'a': 1})
        with open(path, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh), {'a': 1})
        st._atomic_json(path, {'a': 2})   # re-escritura atómica
        with open(path, encoding='utf-8') as fh:
            self.assertEqual(json.load(fh), {'a': 2})
        self.assertFalse(os.path.exists(path + '.tmp'))
        os.unlink(path)


class TestManifestsCache(unittest.TestCase):
    """_load_manifests_cache: solo dict, umbral >= len//2 (not-elim@27,
    GtE@30, 2+1@30)."""
    def _srcs(self, n):
        from conflux.core.state import Source
        return [Source(f'S{i}', f'u{i}') for i in range(n)]

    def _load(self, disk, n_sources=4):
        import io, json
        from unittest import mock
        with mock.patch.object(_sources, 'SOURCES', self._srcs(n_sources)), \
                mock.patch.object(_sources, '_open',
                                  return_value=io.StringIO(json.dumps(disk))):
            return _sources._load_manifests_cache()

    def test_dict_suficiente_devuelve(self):
        out = self._load({'S0': {'a': 1}, 'S1': {'b': 2}})
        self.assertEqual(out, [{'a': 1}, {'b': 2}, None, None])

    def test_umbral_exacto_mitad(self):
        """4 fuentes, 2 manifests: >=len//2 acepta, > lo rechazaría (GtE@30)."""
        self.assertIsNotNone(self._load({'S0': {'a': 1}, 'S2': {'b': 2}}))

    def test_menos_de_mitad_none(self):
        """1 de 4 (//2=2) -> None; //3 (2+1) aceptaría 1."""
        self.assertIsNone(self._load({'S0': {'a': 1}}))

    def test_no_dict_none(self):
        """Lista posicional (formato viejo) -> None (not-elim@27)."""
        self.assertIsNone(self._load([{'a': 1}, {'b': 2}]))


class TestPersistManifests(unittest.TestCase):
    def test_descarta_extra_y_no_dict(self):
        """i<len(SOURCES) y dict (Lt@38, And@38: or incluiría basura/más)."""
        from conflux.core.state import Source
        old = list(_sources.manifests)
        try:
            _sources.manifests[:] = [{'a': 1}, 'basura', {'c': 3}]
            with unittest.mock.patch.object(
                    _sources, 'SOURCES', [Source('S0', 'u'), Source('S1', 'v')]):
                out = _sources._persist_manifests()
        finally:
            _sources.manifests[:] = old
        self.assertEqual(out, {'S0': {'a': 1}})      # idx2 fuera, 'basura' fuera


class TestRefreshManifests(unittest.TestCase):
    def test_fetch_url_y_retry_solo_fallidas(self):
        """manifest.json por fuente (Add@49); retry solo si not m con timeout 20
        (not-elim@55 reintentaría las OK, Add@54)."""
        from unittest import mock
        from conflux.core.state import Source
        urls, timeouts = [], []
        def fake_fetch(url, t):
            urls.append(url); timeouts.append(t)
            return {'ok': 1} if url.startswith('http://a') else None
        old = list(_sources.manifests)
        try:
            with mock.patch.object(_sources, 'SOURCES',
                                   [Source('S0', 'http://a'), Source('S1', 'http://b')]), \
                    mock.patch.object(_sources, 'fetch', fake_fetch), \
                    mock.patch.object(_sources, '_atomic_json'):
                _sources.refresh_manifests()
            self.assertEqual(_sources.manifests[0], {'ok': 1})
            self.assertIsNone(_sources.manifests[1])
        finally:
            _sources.manifests[:] = old
        # el pool lanza ambos fetch a la vez: el orden de llegada es una carrera
        self.assertEqual(set(urls[:2]),
                         {'http://a/manifest.json', 'http://b/manifest.json'})
        retry = [u for u, t in zip(urls, timeouts) if t == 20]
        self.assertEqual(retry, ['http://b/manifest.json'])   # solo la fallida
        self.assertEqual(timeouts[:2], [10, 10])              # mutante 10+1@49


class TestVideoSeAbsEp(unittest.TestCase):
    def test_video_se_por_id_sin_season(self):
        """Sin s/e válidos -> ':S:E' del propio id (And@115, Or@117,
        3+1/GtE/2+1/Add@118)."""
        self.assertEqual(_sources._video_se({'id': 'kitsu:49847:2'}), ':49847:2')
        self.assertIsNone(_sources._video_se({'id': 'a:b'}))       # <3 segs
        self.assertEqual(_sources._video_se({'id': 'x:y:z:w'}), ':z:w')  # [-2:]
        self.assertIsNone(_sources._video_se({}))                   # sin id
        # s dígito + e no-dígito -> cae al path del id (And@115: or -> int('x'))
        self.assertEqual(_sources._video_se(
            {'season': '1', 'episode': 'x', 'id': 'k:9:2:3'}), ':2:3')

    def test_abs_ep_guardas(self):
        """ep multi-seg o no-dígito -> intacto (Or@124); n==len(vs) resuelve el
        último (Lt@127); n=0/fuera de rango -> intacto."""
        vs = [{'season': '1', 'episode': '1'}, {'season': '1', 'episode': '2'}]
        self.assertEqual(_sources._abs_ep(':1:2', vs), ':1:2')
        self.assertEqual(_sources._abs_ep(':abc', vs), ':abc')
        self.assertEqual(_sources._abs_ep(':2', vs), ':1:2')        # último ep
        self.assertEqual(_sources._abs_ep(':0', vs), ':0')
        self.assertEqual(_sources._abs_ep(':3', vs), ':3')          # n > len
        self.assertEqual(_sources._abs_ep(':1', []), ':1')          # sin videos

    def test_resolve_meta_usa_fuente_del_prefijo(self):
        """base = la de _PREFIX_SRC[pref] (Eq@103 elegiría otra fuente)."""
        from unittest import mock
        from conflux.core.state import Source
        _sources._idmap.clear()
        got = []
        with mock.patch.object(_sources, 'SOURCES',
                               [Source('Otra', 'http://x'),
                                Source('Kitsu', 'http://kitsu')]), \
                mock.patch.object(_sources, 'fetch',
                                  lambda u, t: (got.append(u), {'meta': {}})[1]):
            _sources._resolve_meta('series', 'kitsu', '99')
        _sources._idmap.clear()
        self.assertEqual(got, ['http://kitsu/meta/series/kitsu:99.json'])


class TestResPrefixesColon(unittest.TestCase):
    """idPrefixes con ':' final (DesiFlix 'dsx:', MediaFusion 'tmdb:') ->
    normalizados; _id_prefix nunca devuelve colon (R1)."""

    def test_colon_strip(self):
        from conflux.domain.sources import res_prefixes
        m = {'resources': [{'name': 'stream', 'idPrefixes': ['dsx:', 'tt', 'tmdb:']}]}
        self.assertEqual(res_prefixes(m, 'stream'), {'dsx', 'tt', 'tmdb'})

    def test_sin_colon_intacto(self):
        from conflux.domain.sources import res_prefixes
        m = {'resources': [{'name': 'stream', 'idPrefixes': ['tt', 'kitsu']}]}
        self.assertEqual(res_prefixes(m, 'stream'), {'tt', 'kitsu'})

    def test_skip_manifest_dsx(self):
        from conflux.domain.text import _skip_manifest
        m = {'resources': [{'name': 'stream', 'types': ['movie'],
                            'idPrefixes': ['dsx:', 'tt', 'tmdb:']}]}
        self.assertIsNone(_skip_manifest(m, 'movie', 'dsx:c:abc123'))
        self.assertIsNone(_skip_manifest(m, 'movie', 'tt0137523'))
