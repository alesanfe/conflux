#!/usr/bin/env python3
"""Tests del proxy Stremio — corre contra fixtures reales de cada fuente.
Uso: python3 test_proxy.py   (desde ~/apps/conflux)"""
import importlib.util
import json
import os
import unittest

spec = importlib.util.spec_from_file_location('proxy', os.path.join(os.path.dirname(__file__), 'proxy.py'))
px = importlib.util.module_from_spec(spec)
spec.loader.exec_module(px)
import conflux.domain.sources as _px_sources
import conflux.infra.net as _px_net

FIX = os.path.join(os.path.dirname(__file__), 'fixtures')

def fixture(name):
    p = os.path.join(FIX, f'{name}.json')
    if not os.path.exists(p): return []
    with open(p) as f: return json.load(f)


class TestSanitiza(unittest.TestCase):
    """Campos que stremio-web valida estrictamente."""

    def test_fileidx_null_a_cero(self):
        s = {'infoHash': 'a' * 40, 'fileIdx': None, 'title': 'x 1080p'}
        out, *_ = px.norm_stream(s, 'T')
        self.assertEqual(out.get('fileIdx'), 0)   # null→eliminado→setdefault 0 (Torrentio)

    def test_fileidx_bool_no_pasa(self):
        s = {'infoHash': 'a' * 40, 'fileIdx': True, 'title': 'x 1080p'}
        out, *_ = px.norm_stream(s, 'T')
        self.assertIsInstance(out['fileIdx'], int)
        self.assertNotIsInstance(out['fileIdx'], bool)

    def test_fileidx_str_a_int(self):
        out, *_ = px.norm_stream({'infoHash': 'b' * 40, 'fileIdx': '3', 'title': 'x'}, 'T')
        self.assertEqual(out['fileIdx'], 3)

    def test_infohash_lowercase(self):
        out, *_ = px.norm_stream({'infoHash': 'ABCDEF' * 6 + 'ABCD', 'title': 'x'}, 'T')
        self.assertEqual(out['infoHash'], out['infoHash'].lower())

    def test_sin_campos_null(self):
        s = {'infoHash': 'a' * 40, 'title': 'x', 'url': None, 'sources': None}
        out, *_ = px.norm_stream(s, 'T')
        for v in out.values():
            self.assertIsNotNone(v)

    def test_todos_los_fixtures_sanitizados(self):
        for f in os.listdir(FIX):
            for s in fixture(f[:-5]):
                if px._spam(s): continue
                out, _res, _seeds, _size, _cached = px.norm_stream(s, f[:-5])
                for v in out.values():
                    self.assertIsNotNone(v, f'{f}: campo null en {out.get("name")}')
                if 'fileIdx' in out:
                    self.assertIsInstance(out['fileIdx'], int)
                    self.assertNotIsInstance(out['fileIdx'], bool)
                if 'infoHash' in out:
                    self.assertIsInstance(out['infoHash'], str)
                    self.assertEqual(out['infoHash'], out['infoHash'].lower())


class TestSpam(unittest.TestCase):
    def test_orion_cuota_filtrada(self):
        orion = fixture('Orion')
        self.assertTrue(orion and all(px._spam(s) for s in orion),
                        'la entrada de cuota de Orion debe filtrarse')

    def test_notorrent_spam_filtrado(self):
        nt = fixture('NoTorrent')
        spam = [s for s in nt if px._spam(s)]
        ok   = [s for s in nt if not px._spam(s)]
        self.assertGreater(len(spam), 0, 'NoTorrent debe tener spam filtrado')
        self.assertGreater(len(ok), 0, 'NoTorrent debe dejar entradas [FREE]')
        for s in spam:
            self.assertIsNone(s.get('infoHash'))

    def test_titulo_upgrade_legitimo_no_se_filtra(self):
        self.assertFalse(px._spam({'title': 'The Upgrade 2023 1080p WEB-DL 👤 5'}))
        self.assertTrue(px._spam({'title': 'upgrade your account to premium'}))


class TestParseo(unittest.TestCase):
    def test_meteor_usa_behaviorhints(self):
        for s in fixture('Meteor'):
            if px._spam(s): continue
            bh = s.get('behaviorHints', {})
            if isinstance(bh.get('seeders'), int) and not px.parse_seeds(
                    s.get('description', '')):
                out, _res, _seeds, _size, _cached = px.norm_stream(s, 'Meteor')
                self.assertIn(f'👤 {bh["seeders"]}', out['title'])

    def test_resolucion_y_orden(self):
        cases = [('x 2160p', '2160p'), ('x 4K', '2160p'), ('x 720p', '720p'),
                 ('sin resolución', 'SD')]
        for titulo, esperado in cases:
            out, _res, *_ = px.norm_stream({'infoHash': 'a' * 40, 'title': titulo}, 'T')
            self.assertIn(esperado, out['name'])

    def test_codec_no_es_indexer(self):
        s = {'infoHash': 'a' * 40, 'title': 'Movie\n👤36:13 💾5.99 GB ⚙️x264'}
        out, *_ = px.norm_stream(s, 'Ytztvio')
        self.assertIn('Ytztvio', out['title'])
        self.assertNotIn('x264/', out['title'])

    def test_indexer_map_normaliza(self):
        s = {'infoHash': 'a' * 40, 'title': 'Movie 1080p\n👤5 💾1 GB ⚙️ rutracker'}
        out, *_ = px.norm_stream(s, 'T')
        self.assertIn('Rutracker', out['title'])   # casing canónico (RuTor es otro tracker)

    def test_semillas_con_puntos(self):
        s = {'infoHash': 'a' * 40, 'title': 'Movie\n👤13:0 💾800 MB'}
        _out, _res, seeds, *_ = px.norm_stream(s, 'T')
        self.assertEqual(seeds, 13)

    def test_duracion_behaviorhints(self):
        s = {'infoHash': 'a' * 40, 'title': 'x 1080p',
             'behaviorHints': {'duration': 7578}}
        out, *_ = px.norm_stream(s, 'T')
        self.assertIn('2h6m', out['title'])

    def test_formato_compacto_sin_filename(self):
        for f in os.listdir(FIX):
            for s in fixture(f[:-5])[:8]:
                if px._spam(s): continue
                out, *_ = px.norm_stream(s, f[:-5])
                self.assertNotIn('.mkv', out['name'])
                self.assertNotIn('.mkv', out['title'])
                self.assertTrue(out['name'].startswith('☁️'))


class TestDedupYSort(unittest.TestCase):
    def test_dedup_case_insensitive(self):
        state = {'rows': [], 'seen': set(), 'contrib': {},
                 'done': None, 'lock': __import__('threading').Lock()}
        h = 'ABCD' * 10
        for s in [{'infoHash': h, 'title': 'a'}, {'infoHash': h.lower(), 'title': 'b'}]:
            k = str(s.get('infoHash')).lower()
            if k not in state['seen']:
                state['seen'].add(k)
                state['rows'].append(s['title'])
        self.assertEqual(len(state['rows']), 1)

    def test_cache_gana_en_sort(self):
        R = px.RANK
        rows = [('a', '1080P', 50, 1.0, False), ('b', '1080P', 1, 1.0, True)]
        rows.sort(key=lambda r: (-R.get(r[1], 0), -r[4], -r[2], -r[3]))
        self.assertEqual(rows[0][0], 'b')   # cached primero a igual resolución


class TestReglas(unittest.TestCase):
    """Reglas generalizadas extraídas de los bugs reales (ver AGENTS.md)."""

    # --- R1: parseo semántico de ids, nunca posicional ---
    def test_prefijo_ids(self):
        for sid, esperado in [('tt0903747:5:1', 'tt'), ('tt0137523', 'tt'),
                              ('kitsu:49847:2', 'kitsu'), ('kitsu:7442:1:1', 'kitsu'),
                              ('mx:6429078990673560656:kagewani-x', 'mx'),
                              ('_onepace1', '_onepace'), ('tmdb:550', 'tmdb'),
                              ('iptv:24.sk', 'iptv')]:
            self.assertEqual(px._id_prefix(sid), esperado, sid)

    def test_resolve_episodio_absoluto_a_SE(self):
        """kitsu:49847:2 (ep absoluto) -> tt...:1:2 vía videos del meta."""
        px._idmap.clear()
        meta = {'meta': {'imdb_id': 'tt2359704', 'videos': [
            {'id': 'kitsu:49847:1', 'season': '1', 'episode': '1'},
            {'id': 'kitsu:49847:2', 'season': '1', 'episode': '2'}]}}
        orig = _px_sources.fetch
        _px_sources.fetch = _px_net.fetch = lambda *a, **k: meta
        try:
            self.assertEqual(px._resolve_sid('series', 'kitsu:49847:2'), 'tt2359704:1:2')
            self.assertEqual(px._resolve_sid('series', 'kitsu:7442:1:1'), 'tt2359704:1:1')
        finally:
            _px_sources.fetch = _px_net.fetch = orig
            px._idmap.clear()

    # --- R3: el manifest es contrato — idPrefixes debe cubrir los datos reales ---
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
        """Sin estos prefijos la app devuelve 'No addons were requested'."""
        prefs = set(px.build_manifest()['idPrefixes'])
        for p in ('tt', 'kitsu', 'tmdb', 'mx', '_onepace'):
            self.assertIn(p, prefs, f'idPrefixes debe declarar {p}')

    def test_catalog_ids_unicos_y_parseables(self):
        """cid = s<fuente>_<tipo_upstream>_<id>: sin colisiones y reversible."""
        man = self._manifest_sintetico()
        ids = [c['id'] for c in man['catalogs']]
        self.assertEqual(len(ids), len(set(ids)), 'cids duplicados')
        for cid in ids:
            _slug, rest = cid.split('_', 1)
            utype, _, real = rest.partition('_')
            self.assertIn(utype, {'movie', 'series', 'anime', 'channel', 'tv'},
                          f'{cid}: tipo upstream ilegible')
            self.assertTrue(real, f'{cid}: id upstream vacío')

    # --- R2: verificar contenido, no la declaración del tipo ---
    def test_anime_remap_conserva_utype(self):
        man = self._manifest_sintetico()
        anime = [c for c in man['catalogs'] if 'animes_filter' in c['id']]
        self.assertTrue(anime and all(c['type'] == 'anime' for c in anime),
                        'catálogo anime debe servirse como tipo anime')
        self.assertTrue(all(px._UTYPE[c['id']] == 'series' for c in anime),
                        '_UTYPE debe conservar el tipo real para la llamada upstream')

    # --- R4/R6: dedup en fronteras + nombres únicos por (nombre, tipo) ---
    def test_nombres_catalogo_sin_duplicados(self):
        """El mismo nombre puede repetirse en tipos distintos (p. ej. 'Cinemeta'
        movie y series) pero nunca dentro del mismo tipo."""
        man = self._manifest_sintetico()
        pares = [(c['name'], c['type']) for c in man['catalogs']]
        self.assertEqual(len(pares), len(set(pares)),
                         [p for p in pares if pares.count(p) > 1])

    def test_catalog_dupes_upstream_fuera(self):
        """El mismo (tipo,id) upstream no puede emitirse dos veces."""
        man = self._manifest_sintetico()
        self.assertEqual(
            sum(1 for c in man['catalogs'] if 'animes_filter' in c['id']), 1)


class TestInfra(unittest.TestCase):
    def test_res_of_name_o_id(self):
        self.assertIn('stream', px.res_of({'resources': [{'id': 'stream'}]}))
        self.assertIn('stream', px.res_of({'resources': [{'name': 'stream'}]}))
        self.assertIn('stream', px.res_of({'resources': ['stream']}))

    def test_res_types(self):
        m = {'resources': [{'name': 'stream', 'types': ['movie']}]}
        self.assertEqual(px.res_types(m, 'stream'), {'movie'})
        self.assertIsNone(px.res_types(m, 'meta'))

    def test_fuentes_tienen_stream(self):
        px.refresh_manifests()
        for i, (n, b) in enumerate(px.SOURCES):
            m = px.manifests[i] if i < len(px.manifests) else None
            self.assertIsNotNone(m, f'{n}: manifest no responde')


if __name__ == '__main__':
    unittest.main(verbosity=2)
