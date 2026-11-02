"""Unit tests conflux.domain.manifest — contrato R3."""
import unittest

from test import px


class TestReglas(unittest.TestCase):
    """Reglas generalizadas extraídas de los bugs reales (ver AGENTS.md)."""

    def _manifest_sintetico(self):
        old = list(px.manifests)
        px.manifests[:] = [{'resources': ['catalog'], 'catalogs': [
            {'type': 'movie', 'id': 'a', 'name': 'A'},
            {'type': 'series', 'id': 'a', 'name': 'A'},
            {'type': 'series', 'id': 'animes_filter', 'name': 'Anime Filter'},
            {'type': 'series', 'id': 'animes_filter', 'name': 'Anime Filter'},
        ]}] * len(px.SOURCES[:1])
        try:
            return px.build_manifest()
        finally:
            px.manifests[:] = old

    def test_idprefixes_minimos(self):
        prefs = set(px.build_manifest()['idPrefixes'])
        for p in ('tt', 'kitsu', 'tmdb', 'mx', '_onepace'):
            self.assertIn(p, prefs, f'idPrefixes debe declarar {p}')

    def test_catalog_ids_unicos_y_parseables(self):
        man = self._manifest_sintetico()
        ids = [c['id'] for c in man['catalogs']]
        self.assertEqual(len(ids), len(set(ids)), 'cids duplicados')
        for cid in ids:
            _slug, rest = cid.split('_', 1)
            utype, _, real = rest.partition('_')
            self.assertIn(utype, {'movie', 'series', 'anime', 'channel', 'tv'},
                          f'{cid}: tipo upstream ilegible')
            self.assertTrue(real, f'{cid}: id upstream vacío')

    def test_anime_remap_conserva_utype(self):
        man = self._manifest_sintetico()
        anime = [c for c in man['catalogs'] if 'animes_filter' in c['id']]
        self.assertTrue(anime and all(c['type'] == 'anime' for c in anime),
                        'catálogo anime debe servirse como tipo anime')
        self.assertTrue(all(px._UTYPE[c['id']] == 'series' for c in anime),
                        '_UTYPE debe conservar el tipo real para la llamada upstream')

    def test_nombres_catalogo_sin_duplicados(self):
        man = self._manifest_sintetico()
        pares = [(c['name'], c['type']) for c in man['catalogs']]
        self.assertEqual(len(pares), len(set(pares)),
                         [p for p in pares if pares.count(p) > 1])

    def test_catalog_dupes_upstream_fuera(self):
        man = self._manifest_sintetico()
        self.assertEqual(
            sum(1 for c in man['catalogs'] if 'animes_filter' in c['id']), 1)


if __name__ == '__main__':
    unittest.main()
