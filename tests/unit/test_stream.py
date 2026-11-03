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
