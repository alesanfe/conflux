# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.app.stream — /stream + collect."""
import threading
import unittest

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
        key = ('movie', 'tt1', tuple(sorted(S._eff_disabled())))
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
            key_prof = ('movie', 'tt1', tuple(sorted(S._eff_disabled())))
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
                out = debrid._db_usenet('tk', 'https://idx/getnzb/1')
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
            self.assertIsNone(debrid._db_usenet('k', 'http://x/nzb'))
        finally:
            undo()


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
        n0 = len(SOURCES)
        _, undo = cfg_swap(extra_sources=['https://addon.x.com/manifest.json',
                                          'notaurl',
                                          SOURCES[0].base])   # dupe ignorado
        try:
            from conflux.core.cfg import _apply_extra_sources
            _apply_extra_sources()
            self.assertEqual(len(SOURCES), n0 + 1)
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


if __name__ == '__main__':
    unittest.main()
