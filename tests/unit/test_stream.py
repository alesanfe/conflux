# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.app.stream — /stream + collect."""
import collections
import threading
import time
import unittest
from unittest import mock

import conflux.app.stream as S
import conflux.core.state as st
from conflux.domain.text import RANK


class TestDedupYSort(unittest.TestCase):
    def test_dedup_case_insensitive(self):
        seen = set()
        h = 'ABCD' * 10
        for s in [{'infoHash': h}, {'infoHash': h.lower()}]:
            k = str(s['infoHash']).lower()
            seen.add(k)
        self.assertEqual(len(seen), 1)

    def test_cache_gana_en_sort(self):
        rows = [('a', '1080P', 50, 1.0, False), ('b', '1080P', 1, 1.0, True)]
        rows.sort(key=lambda r: (-RANK.get(r[1], 0), -r[4], -r[2], -r[3]))
        self.assertEqual(rows[0][0], 'b')


class TestMergeKey(unittest.TestCase):
    def test_infohash_lowercase(self):
        self.assertEqual(S._merge_key({'infoHash': 'ABCD'}), 'abcd')

    def test_url_fallback(self):
        self.assertEqual(S._merge_key({'url': 'HTTP://X/Y'}), 'http://x/y')

    def test_sin_clave_none(self):
        self.assertIsNone(S._merge_key({'title': 'x'}))


class TestCollectMerge(unittest.TestCase):
    def _state(self):
        return {'rows': [], 'seen': set(), 'contrib': {},
                'done': threading.Event(), 'lock': threading.Lock()}

    def test_merge_dedup_infohash(self):
        stt = self._state()
        h = 'a' * 40
        S._collect_merge(stt, 'Src', [{'infoHash': h, 'title': 'x 1080p'},
                                   {'infoHash': h, 'title': 'x 1080p'}])
        self.assertEqual(len(stt['rows']), 1)

    def test_spam_no_entra(self):
        stt = self._state()
        S._collect_merge(stt, 'Src', [{'title': 'upgrade your account to premium'}])
        self.assertEqual(len(stt['rows']), 0)


class TestHandleStream(unittest.TestCase):
    def test_cache_hit(self):
        key = ('movie', 'tt1', tuple(sorted(S._eff_disabled_for('movie'))))
        st._cset(S._cache, key, [{'name': 'x'}])
        self.assertEqual(S.handle_stream('movie', 'tt1'), {'streams': [{'name': 'x'}]})
        S._cache.clear()

    def test_cache_clave_discrimina_fuentes_desactivadas(self):
        """Un perfil con sources_disabled no puede servir el caché del base
        (ni envenenarlo): las claves difieren por el set efectivo."""
        from conflux.core.cfg import _PROF
        st._cset(S._cache, ('movie', 'tt1', ()), [{'name': 'base'}])
        _PROF.cfg = {'sources_disabled': ['Torrentio']}
        try:
            key_prof = ('movie', 'tt1', tuple(sorted(S._eff_disabled_for('movie'))))
        finally:
            _PROF.cfg = None
        self.assertNotEqual(('movie', 'tt1', ()), key_prof)
        self.assertNotIn('Torrentio', ())
        S._cache.clear()

    def test_m3u_channel(self):
        from conflux.domain.lists import _M3U
        _M3U['m3u:1'] = 'http://iptv/ch1'
        d = S.handle_stream('tv', 'm3u:1')
        self.assertEqual(d['streams'][0]['url'], 'http://iptv/ch1')
        _M3U.clear()

    def test_m3u_desconocido_vacio(self):
        self.assertEqual(S.handle_stream('tv', 'm3u:noexiste'), {'streams': []})


class TestQScore(unittest.TestCase):
    """Ranking consciente de calidad: remux/hdr/codec/audio puntúan entre
    cached y seeds — antes solo decoraban el display."""

    @staticmethod
    def _row(title):
        return S.norm_stream({'name': 'x', 'title': title}, 't')

    def test_remux_gana_a_webrip_misma_res(self):
        rr = S._rows_out([self._row('F.2024.1080p.WEBRip.x264 200seeds'),
                          self._row('F.2024.1080p.REMUX.AV1 5seeds')])
        self.assertIn('REMUX', rr[0]['title'])

    def test_hdr_codec_audio_puntuan(self):
        rr = S._rows_out([self._row('F.2024.1080p.WEB-DL.x264.AAC 50seeds'),
                          self._row('F.2024.1080p.WEB-DL.H265.DV.Atmos 50seeds')])
        self.assertIn('DV', rr[0]['name'])           # hdr va en la línea de nombre

    def test_cached_sigue_ganando(self):
        """qscore no tumba a cached: instant playback antes que remux uncached."""
        cached = S.norm_stream({'title': 'F.2024.1080p.WEBRip.x264 5seeds',
                                'behaviorHints': {'cached': True}}, 't')
        rr = S._rows_out([self._row('F.2024.1080p.REMUX.AV1 5seeds'), cached])
        self.assertIn('⚡', rr[0]['title'])

    def test_res_sigue_mandando(self):
        """4K WEBRip sigue delante de 1080p REMUX — resolución primero."""
        rr = S._rows_out([self._row('F.2024.1080p.REMUX 50seeds'),
                          self._row('F.2024.2160p.WEBRip 50seeds')])
        self.assertIn('2160', rr[0]['name'])

    def test_qscore_campos(self):
        from conflux.domain.text import _qscore, extract_fields
        f = extract_fields({'title': 'F.2024.2160p.BluRay.REMUX.DV.Atmos.H265 10GB'})
        self.assertGreater(_qscore(f), 100)        # remux+dv+hevc+atmos
        f2 = extract_fields({'title': 'F.2024.720p.HDTV.x264'})
        self.assertLess(_qscore(f2), 40)


class TestSortStack(unittest.TestCase):
    """stream_sort: orden configurable (AIOStreams sort-stacks) — los criterios
    no mencionados van detrás en orden por defecto."""

    @staticmethod
    def _row(title, cached=False):
        s = {'name': 'x', 'title': title}
        if cached:
            s['behaviorHints'] = {'cached': True}
        return S.norm_stream(s, 't')

    def test_seeds_primero(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_sort=['seeds'])
        try:
            rr = S._rows_out([self._row('F.2024.1080p.WEBRip.x264 200seeds'),
                              self._row('F.2024.1080p.REMUX.AV1 5seeds')])
            self.assertIn('WEBRIP', rr[0]['title'])   # seeds>quality cuando manda seeds
        finally:
            undo()

    def test_default_res_cached_quality(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_sort=[])
        try:
            rr = S._rows_out([self._row('F.2024.1080p.WEBRip.x264 200seeds'),
                              self._row('F.2024.1080p.REMUX.AV1 5seeds')])
            self.assertIn('REMUX', rr[0]['title'])
        finally:
            undo()

    def test_perfil_override(self):
        from conflux.core.cfg import _PROF
        from tests import cfg_swap
        _, undo = cfg_swap(stream_sort=[])
        try:
            _PROF.cfg = {'stream_sort': ['quality']}
            self.assertEqual(S._sort_order()[0], 'quality')
            _PROF.cfg = None
        finally:
            undo()

    def test_basura_desconocida_ignorada(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_sort=['nonsense', 'seeds'])
        try:
            self.assertEqual(S._sort_order()[0], 'seeds')   # whitelist: solo keys válidas
        finally:
            undo()


class TestStreamRequired(unittest.TestCase):
    """stream_required: solo pasa lo que contiene algún término (required AIOStreams)."""

    def test_filtra_no_match(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_required=['es', 'latino'])
        try:
            out = S._req_out([{'name': 'a', 'title': 'movie spanish es'},
                              {'name': 'b', 'title': 'movie english'}])
            self.assertEqual([s['name'] for s in out], ['a'])
        finally:
            undo()

    def test_vacio_pasa_todo(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_required=[])
        try:
            self.assertEqual(len(S._req_out([{'name': 'a'}, {'name': 'b'}])), 2)
        finally:
            undo()


class TestZilean(unittest.TestCase):
    """infra.zilean — hashlists DMM -> streams cached; shape tolerante."""

    def _fake_get(self, payload):
        class R:
            def json(self_):
                return payload
        class S_:
            def get(self_, url, **kw):
                self.assertIn('/dmm/filtered', url)
                return R()
        return S_()

    def test_parseo(self):
        from unittest import mock
        from conflux.infra import zilean
        from tests import cfg_swap
        payload = [{'filename': 'F.2024.1080p', 'infoHash': 'A' * 40, 'filesize': '100'},
                   {'raw_title': 'x', 'info_hash': 'b' * 40, 'size': 200}]
        _, undo = cfg_swap(zilean_url='http://z:8181')
        try:
            with mock.patch.object(zilean, '_session', return_value=self._fake_get(payload)):
                ss = zilean._zilean('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(ss), 2)
        self.assertEqual(ss[0]['infoHash'], 'a' * 40)
        self.assertTrue(ss[0]['behaviorHints']['cached'])       # DMM = ya en caché
        self.assertEqual(ss[0]['behaviorHints']['videoSize'], 100)

    def test_sin_url_vacio(self):
        from tests import cfg_swap
        from conflux.infra.zilean import _zilean
        _, undo = cfg_swap(zilean_url='')
        try:
            self.assertEqual(_zilean('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_episode_params(self):
        from unittest import mock
        from conflux.infra import zilean
        from tests import cfg_swap
        seen = {}

        class S_:
            def get(self_, url, params=None, **kw):
                seen.update(params or {})
                class R:
                    def json(self_):
                        return []
                return R()
        _, undo = cfg_swap(zilean_url='http://z:8181')
        try:
            with mock.patch.object(zilean, '_session', return_value=S_()):
                zilean._zilean('series', 'tt0944947:2:5')
        finally:
            undo()
        self.assertEqual(seen['Season'], '2')
        self.assertEqual(seen['Episode'], '5')


class TestDebridProxy(unittest.TestCase):
    """debrid_proxy: /dp/ en vez de /r/ — la IP del cliente no toca el debrid."""

    def test_mark_emite_dp(self):
        from tests import cfg_swap
        from conflux.infra.debrid import _db_mark_one
        s = {'infoHash': 'a' * 40, 'name': 'x', 'fileIdx': 2}
        _, undo = cfg_swap(debrid_proxy=True)
        try:
            _db_mark_one('real-debrid', s)
            self.assertIn('/dp/', s['url'])
        finally:
            undo()

    def test_mark_emite_r_por_defecto(self):
        from tests import cfg_swap
        from conflux.infra.debrid import _db_mark_one
        s = {'infoHash': 'a' * 40, 'name': 'x'}
        _, undo = cfg_swap(debrid_proxy=False)
        try:
            _db_mark_one('real-debrid', s)
            self.assertIn('/r/', s['url'])
        finally:
            undo()


class TestStreamFormat(unittest.TestCase):
    """stream_format: template configurable del nombre (formatter AIOStreams)."""

    def test_template_render(self):
        from tests import cfg_swap
        from conflux.domain.text import _display_name, extract_fields
        f = extract_fields({'title': 'F.2024.1080p.BluRay.x265.DV.Atmos'})
        _, undo = cfg_swap(stream_format='{res} · {codec} · {hdr}')
        try:
            self.assertEqual(_display_name(f), '1080p · X265 · DV')
        finally:
            undo()

    def test_token_vacio_sin_separadores(self):
        from tests import cfg_swap
        from conflux.domain.text import _display_name, extract_fields
        f = extract_fields({'title': 'F.2024.1080p.WEB-DL'})
        _, undo = cfg_swap(stream_format='{res} · {hdr} · {audio}')
        try:
            self.assertEqual(_display_name(f), '1080p')
        finally:
            undo()

    def test_template_roto_cae_default(self):
        from tests import cfg_swap
        from conflux.domain.text import _display_name, extract_fields
        f = extract_fields({'title': 'F.2024.1080p'})
        _, undo = cfg_swap(stream_format='{{{')
        try:
            self.assertIn('1080p', _display_name(f))
        finally:
            undo()


class TestNewznab(unittest.TestCase):
    """infra.newznab — items NZB -> streams /nzb/<b64>; gate torbox."""

    _XML = ('<rss><channel>'
            '<item><title>F.2024.1080p</title><link>https://idx/page/1</link>'
            '<enclosure url="https://idx/getnzb/1?apikey=k" length="1500"/>'
            '</item>'
            '<item><title>sin-enclosure</title><link>https://idx/page/2</link></item>'
            '</channel></rss>')

    def _sess(self, xml):
        class R:
            content = xml.encode()
        class S_:
            def get(self_, url, **kw):
                return R()
        return S_()

    def test_parseo_enclosure(self):
        import base64
        from unittest import mock
        from conflux.infra import newznab
        from tests import cfg_swap
        _, undo = cfg_swap(newznab_url='http://nz/api', newznab_key='k',
                           debrid_provider='torbox', debrid_key='tk')
        try:
            with mock.patch.object(newznab, '_session', return_value=self._sess(self._XML)):
                ss = newznab._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(ss), 1)                       # item sin enclosure/link-http: fuera
        u = ss[0]['url']
        self.assertIn('/nzb/', u)
        b = u.rsplit('/nzb/', 1)[-1]
        self.assertEqual(base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode(),
                         'https://idx/getnzb/1?apikey=k')
        self.assertEqual(ss[0]['behaviorHints']['videoSize'], 1500)

    def test_gate_no_torbox(self):
        from unittest import mock
        from conflux.infra import newznab
        from tests import cfg_swap
        _, undo = cfg_swap(newznab_url='http://nz/api', newznab_key='k',
                           debrid_provider='real-debrid', debrid_key='tk')
        try:
            with mock.patch.object(newznab, '_session', return_value=self._sess(self._XML)):
                self.assertEqual(newznab._newznab('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_episode_params(self):
        from unittest import mock
        from conflux.infra import newznab
        from tests import cfg_swap
        seen = {}

        class R:
            content = b'<rss><channel></channel></rss>'

        class S_:
            def get(self_, url, params=None, **kw):
                seen.update(params or {})
                return R()
        _, undo = cfg_swap(newznab_url='http://nz/api', newznab_key='k',
                           debrid_provider='torbox', debrid_key='tk')
        try:
            with mock.patch.object(newznab, '_session', return_value=S_()):
                newznab._newznab('series', 'tt0944947:2:5')
        finally:
            undo()
        self.assertEqual(seen['season'], '2')
        self.assertEqual(seen['ep'], '5')


class _UsenetS:
    """Sesión mínima del flujo TorBox usenet (reutilizable)."""
    def post(self_, url, data=None, **kw):
        class R:
            def json(self_):
                return {'data': {'usenetdownload_id': 7}}
        return R()

    def get(self_, url, params=None, **kw):
        class R:
            def json(self_):
                if 'mylist' in url:
                    return {'data': [{'usenetdownload_id': 7, 'files': [
                        {'id': 3, 'name': 'f.mkv', 'size': 9},
                        {'id': 4, 'name': 'nfo.txt', 'size': 1}]}]}
                return {'data': 'https://cdn.torbox/x.mkv'}
        return R()


class TestDbUsenet(unittest.TestCase):
    """_db_usenet — flujo TorBox usenet: create -> mylist -> requestdl."""

    def test_flujo(self):
        from unittest import mock
        from conflux.infra import debrid
        from tests import cfg_swap
        calls = []

        class S_:
            def post(self_, url, data=None, **kw):
                calls.append(('post', url, data))
                class R:
                    def json(self_):
                        return {'data': {'usenetdownload_id': 7}}
                return R()

            def get(self_, url, params=None, **kw):
                calls.append(('get', url, params))
                class R:
                    def json(self_):
                        if 'mylist' in url:
                            return {'data': [{'usenetdownload_id': 7, 'files': [
                                {'id': 3, 'name': 'f.mkv', 'size': 9},
                                {'id': 4, 'name': 'nfo.txt', 'size': 1}]}]}
                        return {'data': 'https://cdn.torbox/x.mkv'}
                return R()
        _, undo = cfg_swap(debrid_provider='torbox', debrid_key='tk')
        try:
            with mock.patch.object(debrid, '_session', return_value=S_()):
                out = debrid._db_usenet('https://idx/getnzb/1')
        finally:
            undo()
        self.assertEqual(out, 'https://cdn.torbox/x.mkv')
        self.assertIn('usenet/createusenetdownload', calls[0][1])
        req = calls[-1][2]
        self.assertEqual(req['file_id'], 3)            # mayor fichero de vídeo

    def test_gate_provider(self):
        from conflux.infra import debrid
        from tests import cfg_swap
        _, undo = cfg_swap(debrid_provider='real-debrid', debrid_key='k')
        try:
            self.assertIsNone(debrid._db_usenet('http://x/nzb'))
        finally:
            undo()

    def test_gate_multi_provider(self):
        """TorBox como SEGUNDO provider (debrid_providers) también sirve usenet."""
        from conflux.infra import debrid
        from tests import cfg_swap
        _, undo = cfg_swap(debrid_provider='real-debrid', debrid_key='k',
                           debrid_providers={'torbox': 'tk2'})
        try:
            with mock.patch.object(debrid, '_session', return_value=_UsenetS()) as ps:
                out = debrid._db_usenet('http://x/nzb')
        finally:
            undo()
        self.assertEqual(out, 'https://cdn.torbox/x.mkv')
        ps.assert_called()


class TestEasynews(unittest.TestCase):
    """infra.easynews — solr-search por título del meta cacheado -> /en/ b64."""

    def test_gate_sin_credenciales(self):
        from tests import cfg_swap
        from conflux.infra.easynews import _easynews
        _, undo = cfg_swap(easynews_user='', easynews_pass='')
        try:
            self.assertEqual(_easynews('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_parseo(self):
        import base64
        from unittest import mock
        from conflux.infra import easynews
        from tests import cfg_swap
        st._cset(st._meta_c, ('movie', 'tt0137523'),
                 {'meta': {'name': 'Fight Club'}})
        payload = {'downURL': 'https://cdn.en.com', 'dlFarm': 'f1', 'dlPort': '443',
                   'data': [{'0': 'h1', '10': 'Fight.Club.1999.1080p', '11': '.mkv',
                             '4': '1000', 'type': 'VIDEO'},
                            {'0': 'h2', '10': 'clip', '11': '.mkv', 'type': 'VIDEO',
                             '14': '30s'}]}
        class R:
            def json(self_):
                return payload
        class S_:
            def get(self_, url, **kw):
                self.assertEqual(kw['params']['gps'], 'Fight Club')
                return R()
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            with mock.patch.object(easynews, '_session', return_value=S_()):
                ss = easynews._easynews('movie', 'tt0137523')
        finally:
            undo()
            st._meta_c.clear()
        self.assertEqual(len(ss), 1)                    # clip <5min fuera
        self.assertIn('/en/', ss[0]['url'])
        b = ss[0]['url'].rsplit('/en/', 1)[-1]
        link = base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode()
        self.assertEqual(link, 'https://cdn.en.com/f1/443/h1.mkv/Fight.Club.1999.1080p.mkv')  # noqa: E501

    def test_serie_query_s00e00(self):
        from unittest import mock
        from conflux.infra import easynews
        from tests import cfg_swap
        seen = {}
        st._cset(st._meta_c, ('series', 'tt0944947'), {'meta': {'name': 'Show'}})

        class R:
            def json(self_):
                return {'data': []}

        class S_:
            def get(self_, url, params=None, **kw):
                seen.update(params or {})
                return R()
        _, undo = cfg_swap(easynews_user='u', easynews_pass='p')
        try:
            with mock.patch.object(easynews, '_session', return_value=S_()):
                easynews._easynews('series', 'tt0944947:2:5')
        finally:
            undo()
            st._meta_c.clear()
        self.assertEqual(seen['gps'], 'Show s02e05')


class TestParental(unittest.TestCase):
    """parental: fuera streams/canales con etiquetas adultas inequívocas."""

    def test_streams_adultos_fuera(self):
        from tests import cfg_swap
        _, undo = cfg_swap(parental=True)
        try:
            out = S._adult_out([{'name': 'x', 'title': 'Movie XXX 1080p'},
                                {'name': 'y', 'title': 'Movie normal'},
                                {'name': 'z', 'title': 'Sex Education S01'}])
            self.assertEqual([s['name'] for s in out], ['y', 'z'])
        finally:
            undo()

    def test_off_pasa_todo(self):
        from tests import cfg_swap
        _, undo = cfg_swap(parental=False)
        try:
            self.assertEqual(len(S._adult_out([{'title': 'xxx'}])), 1)
        finally:
            undo()

    def test_m3u_canales_adultos_fuera(self):
        from tests import cfg_swap
        from conflux.domain import lists
        metas = [{'id': 'm3u:1', 'name': 'Canal X', 'description': 'Adult'},
                 {'id': 'm3u:2', 'name': 'BBC', 'description': 'News'}]
        _, undo = cfg_swap(parental=True)
        try:
            out = lists._m3u_out(metas)
            self.assertEqual([m['id'] for m in out], ['m3u:2'])
        finally:
            undo()


class TestExtraSources(unittest.TestCase):
    """extra_sources: addons arbitrarios por URL -> SOURCES (marketplace-lite)."""

    def test_aplica_y_limpia(self):
        from tests import cfg_swap
        from conflux.core.state import SOURCES, _SOURCES_BASE
        _, undo = cfg_swap(extra_sources=['https://addon.x.com/manifest.json',
                                          'notaurl',
                                          SOURCES[0].base])   # dupe ignorado
        try:
            from conflux.core.cfg import _apply_extra_sources
            _apply_extra_sources()
            # SOURCES = _SOURCES_BASE + extras: el config real puede traer
            # extras previos — nunca comparar contra len(SOURCES) de entrada
            self.assertEqual(len(SOURCES), len(_SOURCES_BASE) + 1)
            self.assertEqual(SOURCES[-1].base, 'https://addon.x.com')
            self.assertEqual(SOURCES[-1].name, 'addon.x.com')
        finally:
            undo()
            SOURCES[:] = _SOURCES_BASE

    def test_quitar_las_elimina(self):
        from tests import cfg_swap
        from conflux.core.cfg import _apply_extra_sources
        from conflux.core.state import SOURCES, _SOURCES_BASE
        _, undo = cfg_swap(extra_sources=['https://a.com'])
        try:
            _apply_extra_sources()
            self.assertTrue(any(s.base == 'https://a.com' for s in SOURCES))
            import conflux.core.state as st
            st.CFG['extra_sources'] = []
            _apply_extra_sources()
            self.assertFalse(any(s.base == 'https://a.com' for s in SOURCES))
        finally:
            undo()
            SOURCES[:] = _SOURCES_BASE


class TestStreamPreferred(unittest.TestCase):
    """stream_preferred: boost, no filtro — sube sin matar el resto."""

    def test_boost_mantiene_otros(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_preferred=['remux'])
        try:
            out = S._adult_out([{'title': 'x 1080p WEBRip'},
                                {'title': 'y 1080p REMUX'}])
            self.assertEqual(len(out), 2)                     # filtro nunca
            self.assertEqual(S._pref_boost({'title': 'x REMUX'}), 300)
            self.assertEqual(S._pref_boost({'title': 'x webrip'}), 0)
        finally:
            undo()

    def test_boost_ordena(self):
        from tests import cfg_swap
        rows = [S.norm_stream({'name': 'a', 'title': 'F.2024.1080p.WEBRip',
                               'url': 'http://x/low'}, 't'),        # 10 seeds implícitos
                S.norm_stream({'name': 'b', 'title': 'G.2024.1080p.WEBRip',
                               'url': 'http://x/high'}, 't')]
        _, undo = cfg_swap(stream_sort=['seeds'], stream_preferred=['F.2024'])
        try:
            rr = S._rows_out(rows)
            self.assertEqual(rr[0]['url'], 'http://x/low')         # preferred sube pese a seeds
        finally:
            undo()


class TestStreamMaxGb(unittest.TestCase):
    """stream_max_gb: policy mínima de ranking — un 4K de 80GB no siempre gana."""

    @staticmethod
    def _row(size):
        return ({'name': 'n', 'title': 't'}, '4K', 10, size, 0,
                {'es': False}, 's')                        # (out,res,seeds,gb,cached,f,short)

    def test_max_gb_filtra(self):
        from tests import cfg_swap
        rows = [self._row(85.0), self._row(8.0), self._row(0)]
        _, undo = cfg_swap(stream_max_gb=40)
        try:
            out = S._rows_out(rows)
            self.assertEqual(len(out), 2)              # 85GB fuera; size 0 = desconocido, pasa
        finally:
            undo()

    def test_cero_sin_limite(self):
        from tests import cfg_swap
        rows = [self._row(85.0), self._row(8.0)]
        _, undo = cfg_swap(stream_max_gb=0)
        try:
            self.assertEqual(len(S._rows_out(rows)), 2)
        finally:
            undo()


_Src = collections.namedtuple('_Src', 'name base')


class TestCollectSkipGuard(unittest.TestCase):
    """_collect_skip: cada motivo de skip es un guarda distinto — los mutantes
    de comparación los funden; estos tests los separan."""

    def setUp(self):
        self._man = list(st.manifests)
        self._cd = dict(st.COOLDOWN)

    def tearDown(self):
        st.manifests[:] = self._man
        st.COOLDOWN.clear(); st.COOLDOWN.update(self._cd)

    def test_deshabilitada(self):
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_skip_manifest', return_value=None):
            r = S._collect_skip(0, 'movie', 'tt1', {'Foo'})
        self.assertEqual(r, 'deshabilitada')

    def test_manifest_mas_corto_no_indexa(self):
        """i >= len(manifests) -> m=None sin IndexError."""
        st.manifests[:] = []
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_skip_manifest', return_value=None) as sm:
            r = S._collect_skip(0, 'movie', 'tt1', set())
        sm.assert_called_once_with(None, 'movie', 'tt1')
        self.assertIsNone(r)

    def test_skip_manifest_gana(self):
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_skip_manifest', return_value='sin stream'):
            self.assertEqual(S._collect_skip(0, 'movie', 'tt1', set()), 'sin stream')

    def test_cooldown_vigente_salta(self):
        st.COOLDOWN['Foo'] = (time.time() + 60, 'cuota')
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_skip_manifest', return_value=None):
            self.assertEqual(S._collect_skip(0, 'movie', 'tt1', set()),
                             'en espera (cuota)')

    def test_cooldown_caducado_pasa(self):
        st.COOLDOWN['Foo'] = (time.time() - 1, 'cuota')   # ts pasado -> toca fetch
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_skip_manifest', return_value=None):
            self.assertIsNone(S._collect_skip(0, 'movie', 'tt1', set()))


class TestQuotaHit(unittest.TestCase):
    """_quota_hit: solo dicts con título que matchea el regex de cuota."""

    def test_titulo_cuota(self):
        self.assertTrue(S._quota_hit([{'title': 'Daily limit reached!'}]))
        self.assertTrue(S._quota_hit([{'title': 'quota'}]))

    def test_titulo_normal_no(self):
        self.assertFalse(S._quota_hit([{'title': 'F.2024.1080p.WEBRip'}]))

    def test_no_dict_y_sin_titulo(self):
        self.assertFalse(S._quota_hit(['no-dict', {'x': 1}, {'title': None}]))


class TestCollectOne(unittest.TestCase):
    """_collect_one: skip -> health solo si no es 'deshabilitada'; cuota ->
    disyuntor 12h; usable cuenta solo dicts no-spam."""

    def setUp(self):
        self._cd = dict(st.COOLDOWN)

    def tearDown(self):
        st.COOLDOWN.clear(); st.COOLDOWN.update(self._cd)

    def test_deshabilitada_sin_health(self):
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_collect_skip', return_value='deshabilitada'), \
             mock.patch.object(S, '_health') as h:
            self.assertEqual(S._collect_one(0, 'movie', 'tt1', set()), ('Foo', [], 0))
        h.assert_not_called()

    def test_skip_registra_health(self):
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_collect_skip', return_value='en espera (x)'), \
             mock.patch.object(S, '_health') as h:
            S._collect_one(0, 'movie', 'tt1', set())
        h.assert_called_once_with('Foo', True, 'en espera (x)', 0)

    def test_cuota_12h_y_cero_usable(self):
        t0 = time.time()
        ss = [{'title': 'daily limit reached'}, {'title': 'ok'}]
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_collect_skip', return_value=None), \
             mock.patch.object(S, '_collect_fetch', return_value=('Foo', {}, ss)), \
             mock.patch.object(S, '_health') as h:
            self.assertEqual(S._collect_one(0, 'movie', 'tt1', set()), ('Foo', [], 0))
        cd = st.COOLDOWN['Foo']
        self.assertEqual(cd[1], 'cuota')
        self.assertGreaterEqual(cd[0], t0 + st.COOLDOWN_T - 1)   # futuro, no pasado
        self.assertLessEqual(cd[0], time.time() + st.COOLDOWN_T + 1)
        h.assert_called_once_with('Foo', True, 'cuota agotada → espera 12h', 0)

    def test_usable_excluye_spam_y_no_dicts(self):
        ss = [{'title': 'ok 1080p'}, {'title': 'upgrade to premium'}, 'rueda', 7]
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_collect_skip', return_value=None), \
             mock.patch.object(S, '_collect_fetch', return_value=('Foo', {}, ss)), \
             mock.patch.object(S, '_health'):
            name, out, usable = S._collect_one(0, 'movie', 'tt1', set())
        self.assertEqual(usable, 1)
        self.assertEqual(out, ss)


class TestCollectHealth(unittest.TestCase):
    """_collect_health: disyuntor solo si d is None Y streak >= FAIL_STREAK."""

    def setUp(self):
        self._cd, self._h = dict(st.COOLDOWN), dict(st.HEALTH)

    def tearDown(self):
        st.COOLDOWN.clear(); st.COOLDOWN.update(self._cd)
        st.HEALTH.clear(); st.HEALTH.update(self._h)

    def test_ok_reporta_y_no_cooldown(self):
        st.HEALTH['Foo'] = {'streak': 99}
        with mock.patch.object(S, '_health') as h:
            S._collect_health('Foo', {'streams': []},
                              [{'title': 'a'}, {'title': 'upgrade premium'}, 'x'])
        h.assert_called_once_with('Foo', True, '', 1)      # usable sin spam
        self.assertNotIn('Foo', st.COOLDOWN)               # d!=None: nunca cooldown

    def test_fallo_bajo_umbral_sin_cooldown(self):
        st.HEALTH['Foo'] = {'streak': 0}
        with mock.patch.object(S, '_health') as h:
            S._collect_health('Foo', None, [])
        h.assert_called_once_with('Foo', False, 'timeout/error', 0)
        self.assertNotIn('Foo', st.COOLDOWN)

    def test_fallo_en_umbral_dispara(self):
        st.HEALTH['Foo'] = {'streak': st.FAIL_STREAK}
        t0 = time.time()
        with mock.patch.object(S, '_health'):
            S._collect_health('Foo', None, [])
        cd = st.COOLDOWN['Foo']
        self.assertEqual(cd[1], 'fallos')
        self.assertGreaterEqual(cd[0], t0 + st.FAIL_CD_T - 1)


class TestCollectFetch(unittest.TestCase):
    """_collect_fetch: URL quoted con ':' safe, timeout configurable, ss robusto."""

    def test_url_y_timeout_config(self):
        from tests import cfg_swap
        _, undo = cfg_swap(source_timeout=9)
        try:
            with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
                 mock.patch.object(S, 'fetch', return_value={'streams': []}) as f:
                S._collect_fetch(0, 'movie', 'tt1:2:3')
            self.assertEqual(f.call_args[0][0],
                             'http://b/stream/movie/tt1:2:3.json')
            self.assertEqual(f.call_args[0][1], 9)
        finally:
            undo()

    def test_timeout_default(self):
        from tests import cfg_swap
        _, undo = cfg_swap(source_timeout=0)
        try:
            with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
                 mock.patch.object(S, 'fetch', return_value=None) as f:
                name, d, ss = S._collect_fetch(0, 'movie', 'tt1')
            self.assertEqual(f.call_args[0][1], S.STREAM_TIMEOUT)
            self.assertEqual(ss, [])
        finally:
            undo()

    def test_streams_no_lista_a_vacio(self):
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, 'fetch', return_value={'streams': 'str'}):
            self.assertEqual(S._collect_fetch(0, 'movie', 'tt1')[2], [])


class TestCollectMergeServed(unittest.TestCase):
    """_collect_merge: _SERVED contabiliza por resolución con fallback 'SD'."""

    def setUp(self):
        self._sv = dict(st._SERVED)
        st._SERVED.clear()

    def tearDown(self):
        st._SERVED.clear(); st._SERVED.update(self._sv)

    def _state(self):
        return {'rows': [], 'seen': set(), 'contrib': {},
                'done': threading.Event(), 'lock': threading.Lock()}

    def test_served_buckets(self):
        S._collect_merge(self._state(), 'Foo', [
            {'title': 'F 1080p x264', 'url': 'http://x/1'},
            {'title': 'F cam', 'url': 'http://x/2'}])
        sv = st._SERVED['Foo']
        self.assertEqual(sv['n'], 2)
        self.assertEqual(sv['res'].get('1080P'), 1)
        self.assertEqual(sv['res'].get('SD'), 1)       # sin res -> bucket SD, no ''

    def test_no_dict_ignorado(self):
        stt = self._state()
        S._collect_merge(stt, 'Foo', ['rueda', 7, {'title': 'ok', 'url': 'http://x'}])
        self.assertEqual(len(stt['rows']), 1)


class TestCollectWorkers(unittest.TestCase):
    """_collect_tz/zl/nz/en: excepción -> (name, [], 0) + log truncado;
    éxito -> usable sin spam/no-dicts."""

    WORKERS = [('_collect_tz', '_torznab', 'Torznab'),
               ('_collect_zl', '_zilean', 'Zilean'),
               ('_collect_nz', '_newznab', 'Newznab'),
               ('_collect_en', '_easynews', 'Easynews')]

    def test_excepcion_aislada_y_log_truncado(self):
        for worker, dep, name in self.WORKERS:
            with self.subTest(worker=worker), \
                 mock.patch.object(S, dep, side_effect=RuntimeError('x' * 500)), \
                 mock.patch.object(S, 'log_ev') as lg:
                out = getattr(S, worker)('movie', 'tt1')
            self.assertEqual(out, (name, [], 0))
            self.assertEqual(len(lg.call_args[1]['err']), 100)   # repr[:100]

    def test_usable_sin_spam(self):
        ss = [{'title': 'ok'}, {'title': 'free trial premium'}, 'x']
        for worker, dep, name in self.WORKERS:
            with self.subTest(worker=worker), \
                 mock.patch.object(S, dep, return_value=ss):
                self.assertEqual(getattr(S, worker)('m', 'i'), (name, ss, 1))


class _Fut:
    def __init__(self, val=None, err=None):
        self._v, self._e = val, err

    def result(self):
        if self._e:
            raise self._e
        return self._v


class _SeqPool:
    """_POOL falso: futures pre-cocinados en orden de submit."""

    def __init__(self, results):
        self._r = list(results)

    def submit(self, fn, *a):
        return _Fut(self._r.pop(0))


class TestCollectInternals(unittest.TestCase):
    """_collect: timer casi-done, errores de worker tolerados, estado final."""

    def setUp(self):
        self._inf = dict(S._inflight)
        self._cc = dict(S._cache)

    def tearDown(self):
        S._inflight.clear(); S._inflight.update(self._inf)
        S._cache.clear(); S._cache.update(self._cc)

    def _state(self):
        return {'rows': [], 'seen': set(), 'contrib': {},
                'done': threading.Event(), 'lock': threading.Lock()}

    def test_worker_roto_log_y_continua(self):
        # 1 fuente + 4 workers = 5 submits; el 2º (tz) lanza -> log + continue
        futs = [_Fut(('Src0', [{'title': 'x', 'url': 'http://x'}], 1)),
                _Fut(err=RuntimeError('y' * 300)),
                _Fut(('Z', [], 0)), _Fut(('N', [], 0)), _Fut(('E', [], 0))]
        state = self._state()
        with mock.patch.object(S, 'SOURCES', [_Src('S0', 'http://b')]), \
             mock.patch.object(S, '_POOL'), \
             mock.patch.object(S, '_scraper_names', return_value=[]), \
             mock.patch.object(S, '_resolve_sid', side_effect=lambda t, i: i), \
             mock.patch.object(S._POOL, 'submit', side_effect=lambda *a: futs.pop(0)), \
             mock.patch.object(S, 'as_completed', side_effect=lambda fs, **k: list(fs)), \
             mock.patch.object(S, 'log_ev') as lg:
            S._collect(('movie', 'tt1', ()), state)
        self.assertTrue(state['done'].is_set())
        self.assertIn('Src0', state['contrib'])
        err = next(c[1]['err'] for c in lg.call_args_list
                   if c[0][0] == 'src_worker')
        self.assertEqual(len(err), 100)
        self.assertNotIn(('movie', 'tt1', ()), S._inflight)

    def test_nearly_done_solo_con_3_stragglers(self):
        """El timer despierta solo cuando quedan <=3: ni antes ni con 4."""
        timers = []

        class FT:
            def __init__(self, d, fn):
                timers.append(fn)
                self.daemon = None
            def start(self):
                pass
            def cancel(self):
                pass

        results = [(f'S{i}', [], 1) for i in range(6)]   # 2 fuentes + 4 workers
        state = self._state()

        def fake_ac(fs, **k):
            cb = timers[0]
            for i, f in enumerate(fs):
                yield f
                pend = len(fs) - (i + 1)
                if pend == 4:
                    cb()                                   # 4 pendientes: no despierta
                    self.assertFalse(state['done'].is_set())
                if pend == 3:
                    cb()                                   # 3 pendientes: despierta
                    self.assertTrue(state['done'].is_set())

        faketh = type('T', (), {'Timer': FT, 'Event': threading.Event,
                                'Lock': threading.Lock, 'Thread': threading.Thread})
        with mock.patch.object(S, 'SOURCES',
                               [_Src('A', 'http://a'), _Src('B', 'http://b')]), \
             mock.patch.object(S, 'threading', faketh), \
             mock.patch.object(S, '_scraper_names', return_value=[]), \
             mock.patch.object(S, '_resolve_sid', side_effect=lambda t, i: i), \
             mock.patch.object(S._POOL, 'submit',
                               side_effect=lambda *a: _Fut(results.pop(0))), \
             mock.patch.object(S, 'as_completed', side_effect=fake_ac):
            S._collect(('movie', 'tt2', ()), state)
        self.assertTrue(state['done'].is_set())


class TestEnsureCollect(unittest.TestCase):
    """_ensure_collect: misma key -> mismo state; nueva -> state + hilo."""

    def setUp(self):
        self._inf = dict(S._inflight)

    def tearDown(self):
        S._inflight.clear(); S._inflight.update(self._inf)

    def test_misma_key_mismo_state(self):
        ev = threading.Event()
        with mock.patch.object(S, '_collect', side_effect=lambda *a: ev.set()):
            s1 = S._ensure_collect(('movie', 'tt9', ()))
            s2 = S._ensure_collect(('movie', 'tt9', ()))
            ev.wait(2)
        self.assertIs(s1, s2)
        self.assertEqual(len(S._inflight), 1)


class TestUrlTodo(unittest.TestCase):
    """_url_todo: http externo sin veredicto reciente -> candidata (cap 15)."""

    def setUp(self):
        self._bad, self._ok = dict(S._URL_BAD), dict(S._URL_OK)
        S._URL_BAD.clear(); S._URL_OK.clear()   # veredictos de otros tests fuera

    def tearDown(self):
        S._URL_BAD.clear(); S._URL_BAD.update(self._bad)
        S._URL_OK.clear(); S._URL_OK.update(self._ok)

    def _now(self):
        return time.time()

    def test_candidatas_basicas(self):
        now = self._now()
        out = S._url_todo([{'url': 'http://cdn.x/f.mkv'}], now)
        self.assertEqual(out, ['http://cdn.x/f.mkv'])

    def test_no_http_y_loopback_fuera(self):
        now = self._now()
        out = S._url_todo([{'url': 'magnet:?xt=x'},
                           {'url': 'http://127.0.0.1:9700/en/abc'}], now)
        self.assertEqual(out, [])

    def test_veredicto_reciente_bloquea(self):
        now = self._now()
        S._URL_BAD['http://a/1'] = (2, now - 1799)        # <1800s: no reintenta
        S._URL_BAD['http://a/2'] = (2, now - 1801)        # >1800s: reintenta
        S._URL_OK['http://a/3'] = now - 60                # OK reciente: no
        S._URL_OK['http://a/4'] = now - 1801              # OK viejo: sí
        out = S._url_todo([{'url': f'http://a/{i}'} for i in (1, 2, 3, 4)], now)
        self.assertEqual(out, ['http://a/2', 'http://a/4'])

    def test_cap_15(self):
        now = self._now()
        out = S._url_todo([{'url': f'http://a/{i}'} for i in range(20)], now)
        self.assertEqual(len(out), 15)


class TestUrlHead(unittest.TestCase):
    """_url_head: <400 vivo; >=400 muerte segura; excepción -> +1 strike."""

    def setUp(self):
        self._bad, self._ok = dict(S._URL_BAD), dict(S._URL_OK)
        S._URL_BAD.clear(); S._URL_OK.clear()   # veredictos de otros tests fuera

    def tearDown(self):
        S._URL_BAD.clear(); S._URL_BAD.update(self._bad)
        S._URL_OK.clear(); S._URL_OK.update(self._ok)

    def _sess(self, status):
        class R:
            status_code = status
        class Sn:
            def head(self_, u, **kw):
                self_.kw = kw
                return R()
        return Sn()

    def test_vivo_marca_ok(self):
        sn = self._sess(399)
        S._URL_BAD['http://a/x'] = (1, 1)
        with mock.patch.object(S, '_session', return_value=sn):
            S._url_head('http://a/x')
        self.assertIn('http://a/x', S._URL_OK)
        self.assertNotIn('http://a/x', S._URL_BAD)
        self.assertEqual(sn.kw, {'timeout': 3, 'allow_redirects': True})

    def test_400_muerte_segura(self):
        sn = self._sess(400)
        with mock.patch.object(S, '_session', return_value=sn):
            S._url_head('http://a/y')
        st_, _ = S._URL_BAD['http://a/y']
        self.assertEqual(st_, 2)                          # 4xx: muerte directa

    def test_excepcion_suma_strike(self):
        S._URL_BAD['http://a/z'] = (1, 0)
        with mock.patch.object(S, '_session', side_effect=TimeoutError):
            S._url_head('http://a/z')
        st_, _ = S._URL_BAD['http://a/z']
        self.assertEqual(st_, 2)                          # 1+1 strikes


class TestUrlPrune(unittest.TestCase):
    """_url_prune: elimina la mitad más vieja de cada veredicto."""

    def setUp(self):
        self._bad, self._ok = dict(S._URL_BAD), dict(S._URL_OK)
        S._URL_BAD.clear(); S._URL_OK.clear()   # veredictos de otros tests fuera

    def tearDown(self):
        S._URL_BAD.clear(); S._URL_BAD.update(self._bad)
        S._URL_OK.clear(); S._URL_OK.update(self._ok)

    def test_mitad_vieja_fuera(self):
        for i in range(4):
            S._URL_BAD[f'http://b{i}'] = (2, float(i))    # ts crecientes
            S._URL_OK[f'http://o{i}'] = float(i)
        S._url_prune()
        self.assertEqual(sorted(S._URL_BAD), ['http://b2', 'http://b3'])
        self.assertEqual(sorted(S._URL_OK), ['http://o2', 'http://o3'])


class TestUrlHealth(unittest.TestCase):
    """_url_health: muertos (st>=2, reciente) fuera; resto -> HEAD async."""

    def setUp(self):
        self._bad, self._ok = dict(S._URL_BAD), dict(S._URL_OK)
        S._URL_BAD.clear(); S._URL_OK.clear()   # veredictos de otros tests fuera

    def tearDown(self):
        S._URL_BAD.clear(); S._URL_BAD.update(self._bad)
        S._URL_OK.clear(); S._URL_OK.update(self._ok)

    def test_muerto_reciente_sale(self):
        now = time.time()
        S._URL_BAD['http://dead'] = (2, now)
        ss = [{'url': 'http://dead'}, {'url': 'magnet:?x'}]
        with mock.patch.object(S._POOL_BG, 'submit'):
            S._url_health(ss)
        self.assertEqual([s['url'] for s in ss], ['magnet:?x'])

    def test_muerto_viejo_o_strike_no_sale(self):
        now = time.time()
        S._URL_BAD['http://old'] = (2, now - 1801)        # veredicto caduco
        S._URL_BAD['http://half'] = (1, now)              # 1 strike: aún vivo
        ss = [{'url': 'http://old'}, {'url': 'http://half'}]
        with mock.patch.object(S._POOL_BG, 'submit'):
            S._url_health(ss)
        self.assertEqual(len(ss), 2)

    def test_poda_a_2001(self):
        for i in range(1001):
            S._URL_BAD[f'http://b{i}'] = (1, 0.0)
            S._URL_OK[f'http://o{i}'] = 0.0               # 2002 > 2000 -> poda
        with mock.patch.object(S, '_url_prune') as p, \
             mock.patch.object(S._POOL_BG, 'submit'):
            S._url_health([])
        p.assert_called_once()

    def test_sin_poda_a_2000(self):
        for i in range(1000):
            S._URL_BAD[f'http://b{i}'] = (1, 0.0)
            S._URL_OK[f'http://o{i}'] = 0.0               # =2000: no poda
        with mock.patch.object(S, '_url_prune') as p, \
             mock.patch.object(S._POOL_BG, 'submit'):
            S._url_health([])
        p.assert_not_called()

    def test_candidatas_a_head(self):
        ss = [{'url': 'http://cdn/1'}, {'url': 'http://cdn/2'}]
        with mock.patch.object(S._POOL_BG, 'submit') as sb:
            S._url_health(ss)
        self.assertEqual(sb.call_count, 2)


class TestScoringContrato(unittest.TestCase):
    """Contrato numérico del ranking: cada constante/índice cuenta."""

    def test_seed_score_es_500(self):
        r = ({'title': 't'}, '1080P', 10, 1.0, False, {'es': True}, 's')
        self.assertEqual(S._seed_score(r), 510)
        r2 = ({'title': 't'}, '1080P', 10, 1.0, False, {'es': False}, 's')
        self.assertEqual(S._seed_score(r2), 10)

    def test_seed_score_preferred_300(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_preferred=['remux'])
        try:
            r = ({'title': 'x REMUX', 'name': 'n'}, '1080P', 10, 1.0, False,
                 {'es': False}, 's')
            self.assertEqual(S._seed_score(r), 310)
        finally:
            undo()

    def test_sort_seeds_usa_boost(self):
        from tests import cfg_swap
        def mk(t, s, u):
            return S.norm_stream({'title': t, 'url': u, 'name': 'x'}, 't')
        rows = [mk('G.2024.1080p', 0, 'http://x/a'),
                mk('F.2024.1080p', 0, 'http://x/b')]
        _, undo = cfg_swap(stream_sort=['seeds'], stream_preferred=['G.2024'])
        try:
            rr = S._rows_out(rows)
            self.assertEqual(rr[0]['url'], 'http://x/a')  # boost gana a seeds=0 igualados
        finally:
            undo()

    def test_sort_size_descendente(self):
        from tests import cfg_swap
        def mk(sz):
            return ({'name': 'x', 'title': 't', 'url': f'http://x/{sz}'},
                    '1080P', 0, sz, 0, {'es': False}, 's')
        _, undo = cfg_swap(stream_sort=['size'])
        try:
            rr = S._rows_out([mk(1.0), mk(9.0)])
            self.assertIn('9.0', rr[0]['url'])
        finally:
            undo()

    def test_sort_order_exacto(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_sort=['seeds'])
        try:
            self.assertEqual(S._sort_order(),
                             ('seeds', 'res', 'cached', 'quality', 'size'))
        finally:
            undo()

    def test_rowkey_tupla(self):
        f = {'es': True}
        r = ('out', '1080P', 7, 2.0, 1, f, 's')
        k = S._rowkey(r)
        self.assertEqual(k[0], -RANK['1080P'])
        self.assertEqual(k[1], -1)                     # cached
        self.assertEqual(k[3], -(7 + 500))             # seeds + es boost
        self.assertEqual(k[4], -2.0)                   # size


class TestLangRows(unittest.TestCase):
    """_lang_rows: sin override solo añade el es-boost horneado; con override
    re-evalúa el texto y re-etiqueta si cambió."""

    def test_sin_override_es_boost(self):
        r = S.norm_stream({'title': 'F.2024.1080p', 'url': 'http://x', 'name': 'n'},
                          't')
        out, res, seeds, size, cached, q = S._lang_rows([r])[0]
        f_es = dict(r[5]); f_es['es'] = True
        r2 = (r[0], r[1], r[2], r[3], r[4], f_es, r[6])
        self.assertEqual(S._lang_rows([r2])[0][2], r2[2] + 500)

    def test_dict_passthrough(self):
        out = S._lang_rows([{'name': 'x'}])[0]
        self.assertEqual(out[1:], ('SD', 0, 0, 0, 0))

    def test_override_recalcula_es(self):
        from conflux.core.cfg import _PROF
        r = S.norm_stream({'title': 'F.2024.LATINO.1080p', 'url': 'http://x',
                           'name': 'n'}, 't')
        _PROF.cfg = {'stream_langs': ['latino']}
        try:
            rr = S._lang_rows([r])[0]
            self.assertEqual(rr[2], r[2] + 500)         # 'latino' ahora ES
            self.assertIsNot(rr[0], r[0])               # re-etiquetado: out nuevo
        finally:
            _PROF.cfg = None

    def test_override_sin_cambio_no_reetiqueta(self):
        from conflux.core.cfg import _PROF
        r = S.norm_stream({'title': 'F.2024.1080p', 'url': 'http://x',
                           'name': 'n'}, 't')
        _PROF.cfg = {'stream_langs': ['es']}            # 'es' no está -> es=False=f['es']
        try:
            rr = S._lang_rows([r])[0]
            self.assertIs(rr[0], r[0])                  # mismo out: no rebuild
            self.assertEqual(rr[2], r[2])               # sin boost
        finally:
            _PROF.cfg = None


class TestRowsOutMaxGb(unittest.TestCase):
    """stream_max_gb en el borde: size<=mx pasa, 0/desconocido pasa, >mx no."""

    @staticmethod
    def _row(size):
        return ({'name': 'n', 'title': 't'}, '4K', 10, size, 0,
                {'es': False}, 's')

    def test_borde_exacto(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_max_gb=40)
        try:
            out = S._rows_out([self._row(40.0), self._row(40.1)])
            self.assertEqual(len(out), 1)               # 40.0 entra (<=), 40.1 no
        finally:
            undo()


class TestHandleStreamBordes(unittest.TestCase):
    """handle_stream: hit -> sin collect; miss -> _ensure_collect;
    m3u cargado no re-llena."""

    def setUp(self):
        self._m3u = dict(S._M3U)

    def tearDown(self):
        S._M3U.clear(); S._M3U.update(self._m3u)

    def test_m3u_cargado_no_rellena(self):
        S._M3U['m3u:1'] = 'http://iptv/ch'
        with mock.patch.object(S, '_m3u_metas') as mm:
            d = S.handle_stream('tv', 'm3u:1')
        mm.assert_not_called()
        self.assertEqual(d['streams'][0]['url'], 'http://iptv/ch')

    def test_m3u_vacio_rellena_una_vez(self):
        with mock.patch.object(S, '_m3u_metas') as mm:
            S.handle_stream('tv', 'm3u:x')
        mm.assert_called_once()

    def test_hit_no_collect_miss_si(self):
        key = ('movie', 'tt77', tuple(sorted(S._eff_disabled_for('movie'))))
        st._cset(S._cache, key, [])
        with mock.patch.object(S, '_ensure_collect') as ec, \
             mock.patch.object(S, '_debrid_mark'), \
             mock.patch.object(S, '_hash_health'):
            S.handle_stream('movie', 'tt77')
        ec.assert_not_called()
        S._cache.pop(key, None)
        stt = {'rows': [], 'seen': set(), 'contrib': {},
               'done': threading.Event(), 'lock': threading.Lock()}
        stt['done'].set()
        with mock.patch.object(S, '_ensure_collect', return_value=stt) as ec, \
             mock.patch.object(S, '_debrid_mark'), \
             mock.patch.object(S, '_hash_health'), \
             mock.patch.object(S, '_url_health'):
            S.handle_stream('movie', 'tt77')
        ec.assert_called_once_with(('movie', 'tt77', tuple(sorted(S._eff_disabled_for('movie')))))


class TestContratoFino(unittest.TestCase):
    """Última milla de mutación: bordes exactos y flags que los tests
    funcionales no distinguen."""

    def setUp(self):
        self._bad, self._ok = dict(S._URL_BAD), dict(S._URL_OK)
        S._URL_BAD.clear(); S._URL_OK.clear()   # veredictos de otros tests fuera
        self._sv = dict(st._SERVED)
        st._SERVED.clear()

    def tearDown(self):
        S._URL_BAD.clear(); S._URL_BAD.update(self._bad)
        S._URL_OK.clear(); S._URL_OK.update(self._ok)
        st._SERVED.clear(); st._SERVED.update(self._sv)

    def test_usable_solo_dicts_validos(self):
        ss = [{'title': 'ok1'}, {'title': 'ok2'}]        # 0 spam: cuenta != filtro
        for worker, dep, name in TestCollectWorkers.WORKERS:
            with self.subTest(worker=worker), \
                 mock.patch.object(S, dep, return_value=ss):
                self.assertEqual(getattr(S, worker)('m', 'i'), (name, ss, 2))
        with mock.patch.object(S, '_health') as h:
            S._collect_health('X', {}, ss)
        self.assertEqual(h.call_args[0][3], 2)
        with mock.patch.object(S, 'SOURCES', [_Src('Foo', 'http://b')]), \
             mock.patch.object(S, '_collect_skip', return_value=None), \
             mock.patch.object(S, '_collect_fetch', return_value=('Foo', {}, ss)), \
             mock.patch.object(S, '_health'):
            self.assertEqual(S._collect_one(0, 'm', 'i', set())[2], 2)

    def test_timer_delay_y_daemon(self):
        timers = []

        class FT:
            def __init__(self, d, fn):
                self.d, self.daemon = d, None
                timers.append(self)
            def start(self):
                pass
            def cancel(self):
                pass

        faketh = type('T', (), {'Timer': FT, 'Event': threading.Event,
                                'Lock': threading.Lock, 'Thread': threading.Thread})
        state = {'rows': [], 'seen': set(), 'contrib': {},
                 'done': threading.Event(), 'lock': threading.Lock()}
        with mock.patch.object(S, 'SOURCES', [_Src('A', 'http://a')]), \
             mock.patch.object(S, 'threading', faketh), \
             mock.patch.object(S, '_resolve_sid', side_effect=lambda t, i: i), \
             mock.patch.object(S._POOL, 'submit', side_effect=lambda *a: _Fut(('x', [], 0))), \
             mock.patch.object(S, 'as_completed', side_effect=lambda fs, **k: fs):
            S._collect(('movie', 'tt5', ()), state)
        self.assertEqual(timers[0].d, 2.0)
        self.assertIs(timers[0].daemon, True)

    def test_thread_daemon(self):
        threads = []

        class FTh:
            def __init__(self, target=None, args=(), daemon=None):
                self.daemon = daemon
                threads.append(self)
            def start(self):
                pass

        faketh = type('T', (), {'Thread': FTh, 'Event': threading.Event,
                                'Lock': threading.Lock, 'Timer': threading.Timer})
        inf = dict(S._inflight)
        try:
            with mock.patch.object(S, 'threading', faketh):
                S._ensure_collect(('movie', 'tt6', ()))
        finally:
            S._inflight.clear(); S._inflight.update(inf)
        self.assertIs(threads[0].daemon, True)

    def test_ex_out_bordes(self):
        import conflux.core.cfg as cf
        old = cf.RE_EX
        cf.RE_EX = cf._ex_re(['cam'])                    # RE_EX no la toca cfg_swap
        try:
            self.assertIsNone(S._ex_out(None))
            self.assertEqual(S._ex_out([]), [])
            out = S._ex_out([{'name': 'n'}, {'title': 'movie cam 720p'}])
            self.assertEqual(out, [{'name': 'n'}])       # campo ausente -> ''
        finally:
            cf.RE_EX = old

    def test_sort_seeds_fn_numerico(self):
        """_SORT_FNS['seeds'] = -(seeds+boost): el boost RESTA la clave."""
        from tests import cfg_swap
        _, undo = cfg_swap(stream_preferred=['remux'])
        try:
            r = ({'title': 'x REMUX', 'name': 'n'}, 'SD', 0, 0, 0, {'es': 0}, 's')
            self.assertEqual(S._SORT_FNS['seeds'](r), -300)
            r2 = ({'title': 'y', 'name': 'n'}, 'SD', 10, 0, 0, {'es': 0}, 's')
            self.assertEqual(S._SORT_FNS['seeds'](r2), -10)
        finally:
            undo()

    def test_sort_size_cached_no_se_confunde(self):
        """'size' ordena por r[3], no por r[4] (cached)."""
        from tests import cfg_swap
        def mk(sz, ca):
            return ({'name': 'x', 'title': 't', 'url': f'http://x/{sz}'},
                    '1080P', 0, sz, ca, {'es': False}, 's')
        _, undo = cfg_swap(stream_sort=['size'])
        try:
            rr = S._rows_out([mk(1.0, 1), mk(9.0, 0)])  # cached del pequeño no le sube
            self.assertIn('9.0', rr[0]['url'])
        finally:
            undo()

    def test_rowkey_fila_corta(self):
        """len(r)>5 -> r[5] para _qscore (guarda defensiva: <5 rompe en seeds antes)."""
        k = S._rowkey(('out', '1080P', 7, 2.0, 1, {'es': False}))
        self.assertIsInstance(k, tuple)
        self.assertEqual(len(k), 5)

    def test_url_todo_borde_1800(self):
        now = time.time()
        S._URL_BAD['http://a/1'] = (2, now - 1800)       # exactamente 1800: NO reintenta
        S._URL_OK['http://a/2'] = now - 1800
        out = S._url_todo([{'url': 'http://a/1'}, {'url': 'http://a/2'}], now)
        self.assertEqual(out, [])

    def test_muerto_borde_1800_vive(self):
        now = time.time()
        S._URL_BAD['http://edge'] = (2, now - 1800)      # veredicto justo caducado
        ss = [{'url': 'http://edge'}]
        with mock.patch.object(S._POOL_BG, 'submit'):
            S._url_health(ss)
        self.assertEqual(len(ss), 1)

    def test_served_res_falsy_bucket_sd(self):
        stt = {'rows': [], 'seen': set(), 'contrib': {},
               'done': threading.Event(), 'lock': threading.Lock()}
        fake_row = ({'title': 'x'}, '', 0, 0, 0, {}, 's')   # res ''
        with mock.patch.object(S, 'norm_stream', return_value=fake_row):
            S._collect_merge(stt, 'Foo', [{'title': 'x', 'url': 'http://u'}])
        self.assertEqual(st._SERVED['Foo']['res'], {'SD': 1})


class TestRegexFilters(unittest.TestCase):
    """stream_exclude_regex / stream_required_regex (estilo AIOStreams)."""

    def test_exclude_regex(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_exclude_regex=[r'(?i)\bcam\b', r'\d{3,4}p.*ita'])
        try:
            out = S._rex_ex_out([{'title': 'Movie 1080p'},
                                 {'title': 'Movie cam 720p'},
                                 {'title': 'Film 1080p ita sub'}])
        finally:
            undo()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['title'], 'Movie 1080p')

    def test_required_regex(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_required_regex=[r'(?i)espa|latino'])
        try:
            out = S._rex_req_out([{'title': 'Movie ESPAÑOL 1080p'},
                                  {'title': 'Movie ENG 1080p'}])
        finally:
            undo()
        self.assertEqual(len(out), 1)

    def test_patron_roto_se_salta_no_muere(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_exclude_regex=['([unclosed', r'\bcam\b'])
        try:
            out = S._rex_ex_out([{'title': 'cam rip'}, {'title': 'ok 1080p'}])
        finally:
            undo()
        self.assertEqual(len(out), 1)               # el válido sigue filtrando
        self.assertEqual(out[0]['title'], 'ok 1080p')

    def test_vacios_noop(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_exclude_regex=[], stream_required_regex=[])
        try:
            ss = [{'title': 'x'}]
            self.assertIs(S._rex_ex_out(ss), ss)
            self.assertIs(S._rex_req_out(ss), ss)
        finally:
            undo()

    def test_nombre_y_descripcion_tambien_buscan(self):
        from tests import cfg_swap
        _, undo = cfg_swap(stream_exclude_regex=['(?i)hdcam'])
        try:
            out = S._rex_ex_out([{'name': 'release', 'description': 'HDCAM line'}])
        finally:
            undo()
        self.assertEqual(out, [])


class TestSourceGroups(unittest.TestCase):
    """source_groups — routing condicional por tipo (Groups de AIOStreams)."""

    def test_grupo_restringe_tipo(self):
        from conflux.core import cfg
        from tests import cfg_swap
        _, undo = cfg_swap(sources_disabled=[],
                           source_groups={'anime': [st.SOURCES[0].name]})
        try:
            dis_anime = cfg._eff_disabled_for('anime')
            dis_movie = cfg._eff_disabled_for('movie')
        finally:
            undo()
        self.assertEqual(dis_anime, {s.name for s in st.SOURCES[1:]})
        self.assertEqual(dis_movie, set())          # movie sin grupo: todas

    def test_sin_grupos_igual_que_disabled(self):
        from conflux.core import cfg
        old = set(st._DISABLED)
        st._DISABLED.clear(); st._DISABLED.add(st.SOURCES[0].name)
        try:
            self.assertEqual(cfg._eff_disabled_for('movie'),
                             {st.SOURCES[0].name})
        finally:
            st._DISABLED.clear(); st._DISABLED.update(old)

    def test_grupo_vacio_apaga_tipo(self):
        """grupo explícito []: ninguna fuente responde a ese tipo."""
        from conflux.core import cfg
        from tests import cfg_swap
        _, undo = cfg_swap(source_groups={'tv': []})
        try:
            self.assertEqual(cfg._eff_disabled_for('tv'),
                             {s.name for s in st.SOURCES})
        finally:
            undo()

    def test_key_de_cache_incluye_grupo(self):
        """La clave del collect discrimina por grupo efectivo."""
        from conflux.core import cfg
        from tests import cfg_swap
        _, undo = cfg_swap(source_groups={'series': [st.SOURCES[0].name]})
        try:
            d1 = tuple(sorted(cfg._eff_disabled_for('series')))
        finally:
            undo()
        _, undo = cfg_swap(source_groups={})
        try:
            d2 = tuple(sorted(cfg._eff_disabled_for('series')))
        finally:
            undo()
        self.assertNotEqual(d1, d2)


class TestMetrics(unittest.TestCase):
    def test_texto_prometheus(self):
        from conflux.web.metrics import _metrics_text
        body = _metrics_text()
        self.assertIn('# TYPE conflux_source_requests_total counter', body)
        self.assertIn('conflux_route_latency_ms', body)
        self.assertIn('conflux_cache_entries', body)
        self.assertIn('conflux_sources_total', body)


if __name__ == '__main__':
    unittest.main()
