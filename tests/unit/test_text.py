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


class TestQscoreContrato(unittest.TestCase):
    """Mutation-killers: los pesos de calidad SON el ranking — un +1 silencioso
    o un and/or cambiado debe romper algo observable."""
    import conflux.domain.text as T

    def test_anclas_exactas(self):
        """Los pesos son contrato del sort — cambiarlos = decisión, no drift."""
        T = self.T
        self.assertEqual(T._RIP_SC['REMUX'], 80)
        self.assertEqual(T._RIP_SC['WEB-DL'], 55)
        self.assertEqual(T._HDR_SC['DV'], 30)
        self.assertEqual(T._CODEC_SC['AV1'], 15)
        self.assertEqual(T._AUDIO_SC['Atmos'], 25)
        self.assertEqual(T.RANK['2160P'], 8)
        self.assertEqual(T.RANK['1080P'], 6)
        self.assertEqual(T.RANK['720P'], 5)

    def test_rip_orden_estricto(self):
        q = self.T._qscore
        orden = ['BDREMUX', 'BLURAY', 'BDRIP', 'BRRIP', 'WEB-DL', 'WEBRIP',
                 'HDTV', 'DVDRIP', 'DVD']
        sc = [q({'srctype': s}) for s in orden]
        self.assertEqual(sc, sorted(sc, reverse=True))
        self.assertEqual(q({'srctype': 'BDREMUX'}), q({'srctype': 'REMUX'}))

    def test_hdr_codec_audio_orden(self):
        q = self.T._qscore
        self.assertGreater(q({'hdr': 'DV'}), q({'hdr': 'HDR10+'}))
        self.assertGreater(q({'hdr': 'HDR10+'}), q({'hdr': 'HDR10'}))
        self.assertGreater(q({'hdr': 'HDR10'}), q({'hdr': 'HLG'}))
        self.assertGreater(q({'codec': 'av1'}), q({'codec': 'hevc'}))
        self.assertGreater(q({'codec': 'x265'}), q({'codec': 'xvid'}))
        self.assertGreater(q({'audio': 'Atmos'}), q({'audio': 'DTS'}))
        self.assertGreater(q({'audio': 'TrueHD'}), q({'audio': 'EAC3'}))

    def test_audio_max_no_suma(self):
        """max() sobre tokens: Atmos+AAC puntúa como Atmos, no Atmos+AAC."""
        q = self.T._qscore
        self.assertEqual(q({'audio': 'Atmos AAC'}), q({'audio': 'Atmos'}))

    def test_vacio_cero(self):
        self.assertEqual(self.T._qscore({}), 0)
        self.assertEqual(self.T._qscore(
            {'srctype': '???', 'hdr': 'x', 'codec': 'x', 'audio': 'x'}), 0)
        self.assertEqual(self.T._qscore({'audio': None}), 0)   # or -> ''

    def test_tablas_completas(self):
        """Pin de las tablas enteras: un +1 en CUALQUIER peso debe fallar."""
        T = self.T
        self.assertEqual(T._RIP_SC, {
            'BDREMUX': 80, 'REMUX': 80, 'BLURAY': 70, 'BDRIP': 65, 'BRRIP': 60,
            'WEB-DL': 55, 'WEBRIP': 40, 'HDTV': 25, 'DVDRIP': 15, 'DVD': 10})
        self.assertEqual(T._HDR_SC,
            {'DV': 30, 'HDR10+': 28, 'HDR10': 22, 'HLG': 18, 'HDR': 18})
        self.assertEqual(T._CODEC_SC, {
            'AV1': 15, 'HEVC': 12, 'X265': 12, 'H265': 12, 'H.265': 12,
            'AVC': 6, 'X264': 6, 'H264': 6, 'H.264': 6, 'VP9': 6, 'XVID': 0})
        self.assertEqual(T._AUDIO_SC, {
            'Atmos': 25, 'DTS:X': 20, 'TrueHD': 18, 'DTS-HD': 16, 'DTS': 12,
            'EAC3': 10, 'DD+ 5.1': 10, 'DD 5.1': 8, 'DD+': 8, 'FLAC': 10,
            'AAC 2.0': 4, 'AAC': 4, '7.1': 4, '5.1': 2, '2.1': 1})
        self.assertEqual(T.RANK, {
            '4320P': 9, '2160P': 8, '1440P': 7, '1080P': 6, 'HD': 6, '720P': 5,
            '576P': 4, '480P': 3, '360P': 2, '240P': 1, 'SD': 1})


class TestEfLang(unittest.TestCase):
    import conflux.domain.text as T

    def test_multi_con_otro_idioma_es_multi(self):
        """['Multi','Español'] -> 'Multi' solo: mata or->and en _ef_lang."""
        self.assertEqual(self.T._ef_lang('', 'Movie Multi Español 1080p'), 'Multi')

    def test_tres_idiomas_sin_token_es_multi(self):
        self.assertEqual(
            self.T._ef_lang('', 'Movie English Español Portuguese 1080p'), 'Multi')

    def test_dos_idiomas_se_listan(self):
        self.assertEqual(self.T._ef_lang('', 'Movie English Español'),
                         'Inglés / Español')

    def test_un_idioma(self):
        self.assertEqual(self.T._ef_lang('', 'Movie Castellano'), 'Castellano')

    def test_flags_tope_6(self):
        out = self.T._ef_lang('🇪🇸🇬🇧🇫🇷🇩🇪🇮🇹🇵🇹🇯🇵🇰🇷', 'x')
        self.assertEqual(len(out.split()), 6)


class TestEfDur(unittest.TestCase):
    import conflux.domain.text as T

    def test_borde_60s_no_dura(self):
        self.assertEqual(self.T._ef_dur({'duration': 60}, 'x'), '')

    def test_formato_hh_mm(self):
        self.assertEqual(self.T._ef_dur({'duration': 3600}, 'x'), '1h0m')
        self.assertEqual(self.T._ef_dur({'duration': 3660}, 'x'), '1h1m')
        self.assertEqual(self.T._ef_dur({'duration': 7200}, 'x'), '2h0m')
        self.assertEqual(self.T._ef_dur({'duration': 61}, 'x'), '0h1m')
        self.assertEqual(self.T._ef_dur({'duration': 90}, 'x'), '0h1m')

    def test_formato_h_dospuntos_mm(self):
        self.assertEqual(self.T._ef_dur({}, 'x 2:30:00'), '2:30')

    def test_formato_min(self):
        self.assertEqual(self.T._ef_dur({}, 'x 90min'), '90m')

    def test_texto_gana_a_hint(self):
        self.assertEqual(self.T._ef_dur({'duration': 90}, 'x 2h30m'), '2h30m')


class TestEfSize(unittest.TestCase):
    import conflux.domain.text as T

    def test_texto_gana_a_videosize(self):
        self.assertEqual(self.T._ef_size({'videoSize': 9e9}, 'x 1.4 GB'), 1.4)

    def test_videosize_a_gb(self):
        self.assertEqual(self.T._ef_size({'videoSize': 2500000000}, 'x'), 2.5)

    def test_sin_datos_none(self):
        self.assertIsNone(self.T._ef_size({}, 'x'))

    def test_parse_size_unidades(self):
        ps = self.T.parse_size
        self.assertAlmostEqual(ps('700 MB'), 700 / 1024)
        self.assertAlmostEqual(ps('1.5 TB'), 1.5 * 1024)
        self.assertAlmostEqual(ps('1.4 GB'), 1.4)


class TestEfSeedsAudio(unittest.TestCase):
    import conflux.domain.text as T

    def test_seeders_bool_no_es_int(self):
        self.assertNotEqual(self.T._ef_seeds({'seeders': True}, 'x'), True)

    def test_canales_despues_de_marca(self):
        self.assertEqual(self.T._ef_audio('x 5.1 DTS'), 'DTS 5.1')

    def test_tope_3(self):
        out = self.T._ef_audio('x Atmos TrueHD DTS-HD DTS FLAC')
        self.assertEqual(len(out.split()), 3)


class TestCached(unittest.TestCase):
    import conflux.domain.text as T

    def test_tres_vias_de_deteccion(self):
        ef = self.T.extract_fields
        self.assertTrue(ef({'title': 'x 1080p',
                            'behaviorHints': {'cached': True}})['cached'])
        self.assertTrue(ef({'title': 'x ☁️ 1080p'})['cached'])
        self.assertTrue(ef({'title': 'x en debrid y tal'})['cached'])
        self.assertFalse(ef({'title': 'x 1080p'})['cached'])


class TestPrio(unittest.TestCase):
    import conflux.domain.text as T

    def test_normaliza_variantes(self):
        self.assertEqual(self.T._prio(['WEBDL'], self.T.SRCT_ORD), 'WEB-DL')
        self.assertEqual(self.T._prio(['Dolby Vision'], self.T.HDR_ORD), 'DV')

    def test_vacio(self):
        self.assertEqual(self.T._prio([], self.T.SRCT_ORD), '')


class TestIndexerOf(unittest.TestCase):
    import conflux.domain.text as T

    def test_notorrent_host_indexer(self):
        """idx==short + 'Hoster - Notorrent' en texto -> el hoster es el indexer."""
        f = {'indexer': None, 'text': 'Movie x264 1080p\nHurtom - Notorrent'}
        self.assertEqual(self.T._indexer_of(f, 'NoTorrent'), 'Hurtom')

    def test_indexer_codec_cae_a_short(self):
        f = {'indexer': 'x264', 'text': 'Movie'}
        self.assertEqual(self.T._indexer_of(f, 'Ytztvio'), 'Ytztvio')

    def test_indexer_directo_gana(self):
        f = {'indexer': 'RARBG', 'text': 'x'}
        self.assertEqual(self.T._indexer_of(f, 'Ytztvio'), 'RARBG')

    def test_title_no_str_salta_a_name(self):
        """title={} no es str -> extract_fields usa 'name' para el texto."""
        f = self.T.extract_fields({'title': {'x': 1}, 'name': 'Movie 1080p'})
        self.assertEqual(f['res'], '1080P')


class TestSkipManifest(unittest.TestCase):
    """Las 3 guardas de _skip_manifest en ambos sentidos."""
    import conflux.domain.text as T
    M = {'resources': [{'name': 'stream', 'types': ['movie'],
                        'idPrefixes': ['tt']}]}

    def test_camino_feliz_none(self):
        self.assertIsNone(self.T._skip_manifest(self.M, 'movie', 'tt123'))

    def test_tipo_no_soportado(self):
        self.assertEqual(self.T._skip_manifest(self.M, 'series', 'tt123'),
                         'tipo no soportado')

    def test_prefijo_no_soportado(self):
        self.assertEqual(self.T._skip_manifest(self.M, 'movie', 'kitsu:1'),
                         'prefijo no soportado')

    def test_manifest_sin_stream(self):
        m = {'resources': [{'name': 'catalog'}]}
        self.assertEqual(self.T._skip_manifest(m, 'movie', 'tt1'),
                         'manifest sin stream')

    def test_manifest_none_none(self):
        self.assertIsNone(self.T._skip_manifest(None, 'movie', 'tt123'))

    def test_manifest_vacio_none(self):
        self.assertIsNone(self.T._skip_manifest({}, 'movie', 'tt123'))

    def test_stream_sin_types_none(self):
        m = {'resources': [{'name': 'stream'}]}
        self.assertIsNone(self.T._skip_manifest(m, 'movie', 'tt123'))

    def test_emoji_alarma_es_spam(self):
        self.assertTrue(px._spam({'title': '⚠️ premium only'}))
        self.assertTrue(px._spam({'name': '🔒 locked', 'title': 'ok'}))


class TestDisplayT1(unittest.TestCase):
    """_display_t1: el primer token es cached > directo > seeds — orden fijo."""
    import conflux.domain.text as T

    _F = {'cached': False, 'seeds': None, 'size': None, 'codec': ''}

    def test_cached_gana(self):
        self.assertEqual(self.T._display_t1(
            {**self._F, 'cached': True, 'seeds': 9}), ['⚡ En caché'])

    def test_seeds_none_es_directo(self):
        self.assertEqual(self.T._display_t1(self._F), ['🔗 directo'])

    def test_seeds_muestra_numero(self):
        self.assertIn('👤 5', self.T._display_t1({**self._F, 'seeds': 5}))

    def test_size_y_codec_tokens(self):
        out = ' '.join(self.T._display_t1(
            {**self._F, 'seeds': 5, 'size': 1.42, 'codec': 'X265'}))
        self.assertIn('💾 1.4 GB', out)
        self.assertIn('🎞 X265', out)


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
