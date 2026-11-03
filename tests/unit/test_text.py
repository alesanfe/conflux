# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.domain.text — parseo/normalización de streams."""
import os
import unittest

from tests import FIX, fixture, px


class TestSanitiza(unittest.TestCase):
    """Campos que stremio-web valida estrictamente."""

    def test_fileidx_null_a_cero(self):
        s = {'infoHash': 'a' * 40, 'fileIdx': None, 'title': 'x 1080p'}
        out, *_ = px.norm_stream(s, 'T')
        self.assertEqual(out.get('fileIdx'), 0)

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
                if px._spam(s):
                    continue
                out, _res, _seeds, _size, _cached, _f, _sh = px.norm_stream(s, f[:-5])
                for v in out.values():
                    self.assertIsNotNone(v, f'{f}: campo null en {out.get("name")}')
                if 'fileIdx' in out:
                    self.assertIsInstance(out['fileIdx'], int)
                    self.assertNotIsInstance(out['fileIdx'], bool)
                if 'infoHash' in out:
                    self.assertEqual(out['infoHash'], out['infoHash'].lower())


class TestSpam(unittest.TestCase):
    def test_orion_cuota_filtrada(self):
        orion = fixture('Orion')
        self.assertTrue(orion and all(px._spam(s) for s in orion),
                        'la entrada de cuota de Orion debe filtrarse')

    def test_notorrent_spam_filtrado(self):
        nt = fixture('NoTorrent')
        spam = [s for s in nt if px._spam(s)]
        ok = [s for s in nt if not px._spam(s)]
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
            if px._spam(s):
                continue
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
        self.assertIn('Rutracker', out['title'])

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
                if px._spam(s):
                    continue
                out, *_ = px.norm_stream(s, f[:-5])
                self.assertNotIn('.mkv', out['name'])
                self.assertNotIn('.mkv', out['title'])
                self.assertTrue(out['name'].startswith('☁️'))


class TestIdPrefix(unittest.TestCase):
    """R1: parseo semántico de ids, nunca posicional."""

    def test_prefijo_ids(self):
        for sid, esperado in [('tt0903747:5:1', 'tt'), ('tt0137523', 'tt'),
                              ('kitsu:49847:2', 'kitsu'), ('kitsu:7442:1:1', 'kitsu'),
                              ('mx:6429078990673560656:kagewani-x', 'mx'),
                              ('_onepace1', '_onepace'), ('tmdb:550', 'tmdb'),
                              ('iptv:24.sk', 'iptv')]:
            self.assertEqual(px._id_prefix(sid), esperado, sid)


if __name__ == '__main__':
    unittest.main()
