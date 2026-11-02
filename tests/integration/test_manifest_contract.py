# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Integración: build_manifest con manifests sembrados — contrato completo."""
import unittest

import conflux.core.state as st
import conflux.domain.manifest as mf


class TestManifestContract(unittest.TestCase):
    def setUp(self):
        self._m = list(st.manifests)

    def tearDown(self):
        st.manifests[:] = self._m

    def _build(self, manifests):
        st.manifests[:] = manifests
        return mf.build_manifest()

    def test_estructura_base(self):
        man = self._build([])
        for k in ('id', 'version', 'name', 'resources', 'types',
                  'catalogs', 'idPrefixes'):
            self.assertIn(k, man, k)

    def test_recursos_declarados(self):
        man = self._build([])
        self.assertEqual(set(man['resources']), {'catalog', 'meta', 'stream'})

    def test_tipos_declarados(self):
        man = self._build([{'resources': ['catalog'], 'catalogs': [
            {'type': 'movie', 'id': 'a', 'name': 'A'},
            {'type': 'anime', 'id': 'b', 'name': 'B'}]}])
        self.assertIn('movie', man['types'])
        self.assertIn('anime', man['types'])

    def test_synthet_en_manifest(self):
        man = self._build([])
        ids = {c['id'] for c in man['catalogs']}
        self.assertIn('scinemeta_movie_estrenos', ids)
        self.assertIn('suniform_movie_search', ids)


if __name__ == '__main__':
    unittest.main()
