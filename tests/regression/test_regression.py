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
                self.assertIs(cfg._es_pat(), cfg.RE_ES)
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


class TestDebridCacheMutation(unittest.TestCase):
    """Bug: _db_mark_one mutaba los dicts en _cache (url->/r/, infoHash/sources
    popped, '⚡' pegado) — toda petición posterior servía urls debrid incluso
    sin key; además el hit-path ni siquiera aplicaba _debrid_mark."""

    def test_mark_no_muta_objetos_cacheados(self):
        import conflux.infra.debrid as D
        from conflux.core.state import CFG
        prov, key = CFG.get('debrid_provider'), CFG.get('debrid_key')
        CFG['debrid_provider'], CFG['debrid_key'] = 'real-debrid', 'k'
        try:
            D._DB_OK['abc123'] = True
            cached = [{'name': 'T', 'url': 'magnet:?xt=urn:btih:abc123',
                       'infoHash': 'abc123', 'sources': ['dht']}]
            marked = list(cached)
            D._debrid_mark(marked)
            self.assertEqual(cached[0]['url'], 'magnet:?xt=urn:btih:abc123')
            self.assertEqual(cached[0]['infoHash'], 'abc123')
            self.assertTrue(marked[0]['url'].startswith('http://'))
            self.assertNotIn('infoHash', marked[0])
        finally:
            CFG['debrid_provider'], CFG['debrid_key'] = prov, key
            D._DB_OK.pop('abc123', None)


class TestStreamLangsProfileBug(unittest.TestCase):
    """Bug: el flag 'es' y el boost +500 de seeds se horneaban en la fila
    cacheada con el patrón global — un perfil /cfg/ o /p/ con stream_langs
    distinto heredaba sort y etiqueta 'ES' del primer request."""

    def test_perfil_re_etiqueta_y_repuntua(self):
        import conflux.app.stream as S
        import conflux.core.cfg as cfg
        row = S.norm_stream(
            {'name': 'x', 'title': 'Film.2024.1080p.FRENCH.WEB-DL 8seeds'}, 't')
        # global=es -> f['es'] False (title dice FRENCH)
        cfg._PROF.cfg = {'stream_langs': ['french']}
        try:
            rr = S._lang_rows([row])
        finally:
            cfg._PROF.cfg = None
        out, _res, seeds, _size, _cached = rr[0]
        self.assertGreater(seeds, row[2])            # boost por perfil aplicado
        self.assertNotEqual(out['title'], row[0]['title'])  # etiqueta re-hecha
        self.assertIn('ES', out['title'])            # _display_t2 usa el flag

    def test_sin_perfil_mantiene_orden_global(self):
        import conflux.app.stream as S
        import conflux.core.cfg as cfg
        row = S.norm_stream(
            {'name': 'x', 'title': 'Film.2024.1080p.SPANiSH.WEB 8seeds'}, 't')
        cfg._PROF.cfg = None
        rr = S._lang_rows([row])
        self.assertEqual(rr[0][2], row[2] + 500)     # global: boost con flag horneado


class TestRegexLoadOrder(unittest.TestCase):
    """Bug: load_config() corría ANTES de los regex fallback del módulo —
    RE_ES/RE_EX quedaban pisados por \b(es)\b/(?!) hasta el primer /reload.
    Los stream_langs/stream_exclude de config.json no aplicaban al arrancar."""

    def test_re_es_deriva_de_config_en_import(self):
        import conflux.core.cfg as cfg
        langs = cfg.CFG.get('stream_langs') or ['es']
        for term in langs:
            self.assertIn(term, cfg.RE_ES.pattern)   # tras import, no tras /reload

    def test_re_ex_deriva_de_config_en_import(self):
        import conflux.core.cfg as cfg
        terms = cfg.CFG.get('stream_exclude') or []
        if terms:
            self.assertNotEqual(cfg.RE_EX.pattern, '(?!)')
        else:
            self.assertEqual(cfg.RE_EX.pattern, '(?!)')


class TestConfigureCastBug(unittest.TestCase):
    """Bugs: int(form['calendar_months_ahead']) lanzaba ValueError con 'abc'
    (conexión cortada, no 500); y un POST parcial borraba TODO lo ausente
    (docstring decía 'solo campos presentes pisan' — no era verdad: 'src'
    ausente desactivaba las 23 fuentes, torznab_url/trakt_client_id/m3u_path
    quedaban en '')."""

    def _save(self, form, check):
        from unittest.mock import patch
        from conflux.core.state import CFG
        from conflux.web.pages import _configure_save
        backup = CFG.copy()
        try:
            with patch('conflux.web.pages._save_cfg'),                     patch('conflux.web.pages.load_config'):
                _configure_save(form)
            check(CFG)
        finally:
            CFG.clear()
            CFG.update(backup)

    def test_configure_save_no_explotaba_con_texto(self):
        self._save({'calendar_months_ahead': ['abc']},
                   lambda c: self.assertIsInstance(c.get('calendar_months_ahead'), int))

    def test_configure_save_acota_meses(self):
        self._save({'calendar_months_ahead': ['999']},
                   lambda c: self.assertEqual(c['calendar_months_ahead'], 24))
        self._save({'calendar_months_ahead': ['-3']},
                   lambda c: self.assertEqual(c['calendar_months_ahead'], 1))

    def test_post_parcial_no_borra_ni_desactiva(self):
        from conflux.core.state import CFG
        def chk(c):
            self.assertEqual(c.get('sources_disabled'), CFG.get('sources_disabled'))
            self.assertEqual(c.get('m3u_path'), CFG.get('m3u_path'))
        # POST solo con 'months': ni sources_disabled (todas) ni m3u_path/torznab cambian
        self._save({'calendar_months_ahead': ['3']},
                   lambda c: (self.assertEqual(c.get('sources_disabled'), []),
                              self.assertNotEqual(c.get('m3u_path'), '') if CFG.get('m3u_path') else None))


class TestReloadSecretsBug(unittest.TestCase):
    """Bug: POST /reload logueaba CFG completo al conflux.log y su respuesta
    solo enmascaraba claves con 'key' — trakt_access_token, client_secret,
    opensubtitles_pass y admin_token quedaban en claro en log y JSON."""

    def test_secrets_nunca_en_claro(self):
        from conflux.web.server import _cfg_public
        out = _cfg_public({'trakt_access_token': 'AT', 'trakt_refresh_token': 'RT',
                           'trakt_client_secret': 'CS', 'opensubtitles_pass': 'OP',
                           'debrid_key': 'DK', 'admin_token': 'AD',
                           'stream_langs': ['es'], 'm3u_path': '/x'})
        for s in ('AT', 'RT', 'CS', 'OP', 'DK', 'AD'):
            self.assertNotIn(s, str(out), s)
        self.assertEqual(out['stream_langs'], ['es'])           # no-secret intacto
        self.assertEqual(out['trakt_access_token'], '***')


class TestRpdbMissBug(unittest.TestCase):
    """Bug: _rpdb pasó a no-destructivo pero catalog/meta descartaban su
    retorno — en cache-miss la respuesta salía SIN poster rpdb; solo los
    hits lo llevaban. Además el caché no debe guardar la versión marcada
    (la key es por perfil)."""

    def test_miss_aplica_rpdb_y_cachea_limpio(self):
        import conflux.app.meta as M
        import conflux.core.state as st
        from tests import patch_fetch
        st._meta_c.clear()
        key = st.CFG.get('rpdb_key')
        st.CFG['rpdb_key'] = 'K9'
        self._m = list(st.manifests)
        st.manifests[:] = [{'resources': [
            {'name': 'meta', 'types': ['movie']}]}]
        try:
            restore = patch_fetch(lambda *a, **k: {
                'meta': {'id': 'tt0137523', 'poster': 'http://orig/p.jpg'}})
            try:
                from unittest.mock import patch
                seen = []
                with patch.object(M, '_ensure_collect', seen.append):  # prefetch: hermético
                    d = M.handle_meta('movie', 'tt0137523')
                self.assertEqual(len(seen[0]), 3,            # (stype,sid,dis): la tupla
                                 'prefetch con clave vieja -> ValueError en _collect')
                self.assertEqual(seen[0][:2], ('movie', 'tt0137523'))
            finally:
                restore()
            self.assertIn('ratingposterdb.com/K9', d['meta']['poster'])
            self.assertEqual(st._meta_c[('movie', 'tt0137523')][1]['meta']['poster'],
                             'http://orig/p.jpg')          # caché limpio
        finally:
            st.CFG['rpdb_key'] = key
            st.manifests[:] = self._m
            st._meta_c.clear()


class TestManifestCacheAlignment(unittest.TestCase):
    """Bug latente: el manifest-cache se alineaba POSICIONALMENTE — editar
    sources.json entre arranques dejaba manifests[i] apuntando a otra fuente
    (routing equivocado hasta el refresh). Ahora va por nombre."""

    def _cache(self, payload):
        import json, tempfile, os
        import conflux.domain.sources as SO
        fd, p = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f)
        old = SO._MF_CACHE
        SO._MF_CACHE = p
        try:
            return SO._load_manifests_cache()
        finally:
            SO._MF_CACHE = old
            os.unlink(p)

    def test_alinea_por_nombre_no_posicion(self):
        import conflux.domain.sources as SO
        import conflux.core.state as st
        # disco: orden INVERSO al de SOURCES — por índice saldría cruzado
        disk = {s.name: {'id': f'id.{s.name}'} for s in reversed(st.SOURCES)}
        out = self._cache(disk)
        for i, s in enumerate(st.SOURCES):
            self.assertEqual(out[i]['id'], f'id.{s.name}')

    def test_formato_viejo_lista_se_descarta(self):
        self.assertIsNone(self._cache([{'id': 'x'}] * 30))

    def test_fuente_borrada_no_desalinea(self):
        import conflux.core.state as st
        disk = {s.name: {'id': f'id.{s.name}'} for s in st.SOURCES[:-1]}
        out = self._cache(disk)
        self.assertEqual(out[-1], None)            # nueva fuente: hueco, no cruce
        for i, s in enumerate(st.SOURCES[:-1]):
            self.assertEqual(out[i]['id'], f'id.{s.name}')


class TestUrlHealthBug(unittest.TestCase):
    """Bug: _url_todo excluía TODA url presente en _URL_BAD sin mirar su
    timestamp — el 'reintento a los 30min' del docstring no existía: un
    timeout transitorio quedaba sin re-verificar para siempre y un 4xx
    caduco de CDN no podía revivir nunca."""

    def _health(self, bad_entry, url='http://cdn.example/f.mp4'):
        import time as tm
        from unittest.mock import patch
        import conflux.app.stream as S
        sent = []
        S._URL_BAD.clear(); S._URL_OK.clear()
        S._URL_BAD[url] = bad_entry
        streams = [{'url': url}]
        with patch.object(S._POOL_BG, 'submit', lambda f, u: sent.append(u)):
            S._url_health(streams)
        return streams, sent

    def test_strike_viejo_se_reverifica(self):
        import time as tm
        streams, sent = self._health((1, tm.time() - 2000))
        self.assertEqual(sent, ['http://cdn.example/f.mp4'])   # reintento a los 30min

    def test_muerto_viejo_puede_revivir(self):
        import time as tm
        streams, sent = self._health((2, tm.time() - 2000))
        self.assertEqual(sent, ['http://cdn.example/f.mp4'])
        self.assertEqual(len(streams), 1)                       # ya no es 'dead' fresco

    def test_muerto_fresco_se_filtra_sin_rehead(self):
        import time as tm
        streams, sent = self._health((2, tm.time()))
        self.assertEqual(sent, [])
        self.assertEqual(streams, [])                           # filtrado mientras es fresco
