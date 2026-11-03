# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,too-few-public-methods,attribute-defined-outside-init,duplicate-code,non-ascii-name,wrong-import-position
"""Regression tests: cada bug real encontrado queda fijado con su contexto.

Si un refactor reintroduce el bug, el nombre del test dice exactamente qué
se rompió y por qué importaba."""
import unittest

import conflux.app.meta as M
import conflux.core.state as st
import conflux.domain.catalog as C
import conflux.domain.text as T
import conflux.infra.torznab as TZ
from tests import patch_fetch


class TestIdImpedanceBugs(unittest.TestCase):
    """R1: impedancia de ids — los bugs de routing más costosos."""

    def test_tt_no_es_prefijo_posicional(self):
        """Bug: 'tt0903747:1:1' se leía como prefijo 'tt0903747' → series rotas
        aunque movies funcionaban. El prefijo son las letras iniciales."""
        self.assertEqual(T._id_prefix('tt0903747:1:1'), 'tt')
        self.assertEqual(T._id_prefix('kitsu:49847:2'), 'kitsu')
        self.assertEqual(T._id_prefix('mx:6429:slug'), 'mx')
        self.assertEqual(T._id_prefix('_onepace1'), '_onepace')

    def test_episodio_absoluto_kitsu_via_videos(self):
        """kitsu:49847:2 (ep absoluto) → tt:1:2 usando videos[] del meta
        upstream como autoridad — nunca aritmética propia."""
        meta = {'meta': {'imdb_id': 'tt2359704', 'videos': [
            {'id': 'kitsu:49847:1', 'season': 1, 'episode': 1},
            {'id': 'kitsu:49847:2', 'season': 1, 'episode': 2}]}}
        restore = patch_fetch(lambda *a, **k: meta)
        try:
            import conflux.domain.sources as SO
            SO._idmap.clear()
            sid = SO._resolve_sid('series', 'kitsu:49847:2')
            self.assertTrue(sid.endswith(':1:2'), sid)
        finally:
            restore()


class TestEncodingBugs(unittest.TestCase):
    def test_comas_no_doble_encoding(self):
        """Bug: urlencode sin safe=',' → %2C→%252C y upstream recibía UN solo
        id mutilado → metasDetailed:[null]. Las comas deben llegar literales."""
        captured = []
        def fake(url, t=7):
            captured.append(url)
            return {'metasDetailed': []}
        restore = patch_fetch(fake)
        try:
            C.handle_catalog(
                'series', 'scinemeta_series_top',
                'calendarVideosIds=tt0944947,tt0903747')
        finally:
            restore()
        self.assertTrue(captured, 'no se llamó upstream')
        for u in captured:
            self.assertNotIn('%252C', u, 'doble encoding reapareció')


class TestMetaClosureBug(unittest.TestCase):
    def test_want_loop_no_captura_el_ultimo(self):
        """Bug: closure sobre la variable del bucle — todos los fetches de meta
        usaban el ÚLTIMO 'want'. Cada intento (series→movie fallback) debe
        llamar con su propio tipo."""
        st._meta_c.clear()
        self._m = list(st.manifests)
        st.manifests[:] = [{'resources': [
            {'name': 'meta', 'types': ['movie', 'series']}]}]
        seen = []
        def fake(url, t=4):
            seen.append(url)
            return {'meta': {}}          # vacío → handle_meta reintenta tipos
        restore = patch_fetch(fake)
        try:
            M.handle_meta('series', 'tt_cualquiera')
        finally:
            restore()
        st.manifests[:] = self._m
        st._meta_c.clear()
        wants = {u.split('/meta/')[1].split('/')[0] for u in seen
                 if '/meta/' in u}
        self.assertIn('series', wants)


class _FakeResp:
    content = b'<rss><channel/></rss>'


class TestTorznabQueryBug(unittest.TestCase):
    def test_movie_usa_t_movie_no_tvsearch(self):
        """Bug: movies consultaban t=tvsearch → Prowlarr devolvía series.
        movie→t=movie, series→t=tvsearch."""
        if not TZ._ET:
            self.skipTest('defusedxml no instalado')
        seen = []
        class _Sess:
            def get(self, url, params=None, timeout=None):
                seen.append(params)
                return _FakeResp()
        orig = TZ._session
        TZ._session = lambda netloc: _Sess()
        try:
            TZ._tz_query('http://idx/api', 'k', 'movie', 'tt1')
            TZ._tz_query('http://idx/api', 'k', 'series', 'tt1')
        finally:
            TZ._session = orig
        self.assertEqual(seen[0]['t'], 'movie')
        self.assertEqual(seen[1]['t'], 'tvsearch')


class TestStreamExcludeBugs(unittest.TestCase):
    """Bugs: 'ts'/'cam' como substring mataba release-groups legítimos
    (BTS, CATS, BOOTS, CAMERA); y _ex_re usaba _RE_NEVER antes de existir
    (NameError al importar cfg con stream_exclude vacío → proxy no arrancaba)."""

    def test_import_cfg_no_dependera_de_orden_modulo(self):
        """_ex_re([]) no puede referenciar _RE_NEVER (definido al final del módulo)."""
        import conflux.core.cfg as cfg
        self.assertEqual(cfg._ex_re([]).pattern, '(?!)')
        self.assertEqual(cfg._ex_re(['ts']).pattern,
                         r'(?:(?<![a-z0-9])ts(?![a-z0-9]))')

    def test_terminos_cortos_con_borde_no_substring(self):
        import conflux.core.cfg as cfg
        import conflux.app.stream as S
        orig = cfg.RE_EX
        try:
            cfg.RE_EX = cfg._ex_re(['cam', 'camrip', 'hdts', 'ts', 'screener'])
            for t in ('Movie.2024.1080p.CAM.XViD', 'Movie.2024.HDTS.x264',
                      'Movie.2024.TS.H264', 'Movie.2024.CAMRip.x264',
                      'Movie.2024.SCREENER.x264', 'Movie.2024.CAM.X264-CAT'):
                self.assertEqual(S._ex_out([{'name': 'X', 'title': t}]), [], t)
            for t in ('Concierto.BTS.2023.1080p', 'Movie.2024.1080p-CATS',
                      'Movie.2024.BluRay-BOOTS', 'Scary.Movie.CAMERA.2000',
                      'Movie.2024.WEB-DL-GOMAW'):
                self.assertEqual(len(S._ex_out([{'name': 'X', 'title': t}])), 1, t)
        finally:
            cfg.RE_EX = orig


class TestCfgUrlTypeBugs(unittest.TestCase):
    """Bugs: /cfg/ solo validaba claves, no tipos — stream_langs=5 pasaba el
    whitelist y _es_pat() iteraba un int (TypeError en pleno parseo de stream);
    valores str iteraban por caracteres silenciosamente."""

    def test_perfil_con_tipos_malos_cae_al_global(self):
        import conflux.core.cfg as cfg
        for bad in ({'stream_langs': 5}, {'stream_langs': 'es'},
                    {'stream_exclude': 'cam'}, {'stream_exclude': ['a', 1]}):
            cfg._PROF.cfg = bad
            try:
                self.assertEqual(cfg._es_pat(), cfg.RE_ES.pattern)
                self.assertIs(cfg._ex_pat(), cfg.RE_EX)
            finally:
                cfg._PROF.cfg = None

    def test_whitelist_valida_tipos(self):
        from conflux.web.server import H
        self.assertTrue(H._cfg_url_ok('stream_langs', ['es']))
        self.assertFalse(H._cfg_url_ok('stream_langs', 'es'))
        self.assertFalse(H._cfg_url_ok('stream_langs', 5))
        self.assertFalse(H._cfg_url_ok('stream_langs', ['es', 5]))
        self.assertTrue(H._cfg_url_ok('calendar_months_ahead', 7))
        self.assertFalse(H._cfg_url_ok('calendar_months_ahead', '7'))
        self.assertFalse(H._cfg_url_ok('calendar_months_ahead', True))  # bool es int
        self.assertTrue(H._cfg_url_ok('rpdb_key', 'K9'))
        self.assertFalse(H._cfg_url_ok('admin_token', 'x'))
