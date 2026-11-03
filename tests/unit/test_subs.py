# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.app.subs — OpenSubtitles."""
import unittest
from unittest.mock import MagicMock, patch

import conflux.app.subs as sub
from conflux.core import cfg
from tests import cfg_swap, fake_session


class TestSubsQuery(unittest.TestCase):
    def test_tt_directo(self):
        q = sub._subs_query('movie', 'tt0137523')
        self.assertEqual(q['imdb_id'], '0137523')
        self.assertIn('es', q['languages'])

    def test_series_s_e(self):
        q = sub._subs_query('series', 'tt0944947:1:1')
        self.assertEqual(q['season_number'], '1')
        self.assertEqual(q['episode_number'], '1')

    def test_tt_no_llama_resolve(self):
        """sid tt → NO se llama a _resolve_sid (early-return gratis)."""
        with patch.object(sub, '_resolve_sid') as rs:
            q = sub._subs_query('movie', 'tt0137523')
        rs.assert_not_called()
        self.assertEqual(q, {'imdb_id': '0137523', 'languages': 'es,en'})

    def test_alien_resuelve_args_exactos(self):
        """sid no-tt → _resolve_sid(stype, sid) y se usa el [0] de su split(':')."""
        with patch.object(sub, '_resolve_sid', return_value='tt555:9:9') as rs:
            q = sub._subs_query('series', 'kitsu:49847:2')
        rs.assert_called_once_with('series', 'kitsu:49847:2')
        self.assertEqual(q['imdb_id'], '555')

    def test_resolve_no_tt_none(self):
        """resolución que no llega a imdb → None (la función devuelve None)."""
        with patch.object(sub, '_resolve_sid', return_value='kitsu:49847'):
            self.assertIsNone(sub._subs_query('series', 'kitsu:49847:2'))

    def test_lstrip_solo_t(self):
        """lstrip('t'): tras 'tt' no se mutilan otros chars."""
        q = sub._subs_query('movie', 'ttX5')
        self.assertEqual(q['imdb_id'], 'X5')

    def test_movie_con_partes_no_recibe_season(self):
        q = sub._subs_query('movie', 'tt0137523:1:2')
        self.assertNotIn('season_number', q)

    def test_series_sin_ep_no_season(self):
        q = sub._subs_query('series', 'tt0137523:9')
        self.assertNotIn('season_number', q)

    def test_season_episode_distintos(self):
        q = sub._subs_query('series', 'tt0944947:1:2')
        self.assertEqual((q['season_number'], q['episode_number']), ('1', '2'))


class TestSubsMap(unittest.TestCase):
    def _r(self, data):
        m = MagicMock(); m.json.return_value = {'data': data}; return m

    def test_es_first(self):
        r = self._r([
            {'attributes': {'language': 'en', 'files': [{'file_id': 2}]}},
            {'attributes': {'language': 'es', 'files': [{'file_id': 1}]}}])
        out = sub._subs_map(r)
        self.assertEqual(out[0]['lang'], 'es')

    def test_sin_file_id_fuera(self):
        r = self._r([{'attributes': {'language': 'es', 'files': []}}])
        self.assertEqual(sub._subs_map(r), [])

    def test_lang_ausente_default_es(self):
        r = self._r([{'attributes': {'files': [{'file_id': 1}]}}])
        out = sub._subs_map(r)
        self.assertEqual(out[0]['lang'], 'es')

    def test_otros_idiomas_conservados(self):
        """language != es se conserva tal cual (no se normaliza a es)."""
        r = self._r([{'attributes': {'language': 'en', 'files': [{'file_id': 1}]}},
                     {'attributes': {'language': 'pt', 'files': [{'file_id': 2}]}}])
        self.assertEqual([x['lang'] for x in sub._subs_map(r)], ['en', 'pt'])


class TestHandleSubs(unittest.TestCase):
    def test_sin_key_vacio(self):
        with patch.object(cfg, '_cfgget', return_value=None):
            self.assertEqual(sub.handle_subtitles('movie', 'tt1'), {'subtitles': []})

    def test_sin_key_no_llama_api(self):
        """Sin key ni siquiera se consulta _subs_query/_session."""
        with patch.object(cfg, '_cfgget', return_value=None), \
                patch.object(sub, '_session') as sess:
            self.assertEqual(sub.handle_subtitles('movie', 'tt1'), {'subtitles': []})
        sess.assert_not_called()

    def test_pasa_stype_a_query(self):
        _, undo = cfg_swap(opensubtitles_key='K')
        s, _ = fake_session({'json': {'data': []}})
        try:
            with patch.object(sub, '_session', return_value=s), \
                    patch.object(sub, '_subs_query', return_value={'imdb_id': '1'}) as q:
                sub.handle_subtitles('series', 'tt5:1:2')
        finally:
            undo()
        q.assert_called_once_with('series', 'tt5:1:2')

    def test_llamada_exacta(self):
        """URL, params, headers y timeout de la API son los del contrato."""
        _, undo = cfg_swap(opensubtitles_key='K')
        s, calls = fake_session({'json': {'data': []}})
        try:
            with patch.object(sub, '_session', return_value=s) as sess:
                sub.handle_subtitles('movie', 'tt0137523')
        finally:
            undo()
        sess.assert_called_once_with('api.opensubtitles.com')
        self.assertEqual(calls, [('GET',
            'https://api.opensubtitles.com/api/v1/subtitles',
            {'params': {'imdb_id': '0137523', 'languages': 'es,en'},
             'headers': {'Api-Key': 'K', 'User-Agent': 'conflux 1.0'},
             'timeout': 8})])


if __name__ == '__main__':
    unittest.main()




class TestSubsMapLang(unittest.TestCase):
    def test_es_first_y_urls_propias(self):
        s, _ = fake_session({'json': {'data': [
            {'attributes': {'language': 'eng', 'files': [{'file_id': 2}]}},
            {'attributes': {'language': 'es', 'files': [{'file_id': 1}]}},
            {'attributes': {'language': 'por', 'files': []}}]}})
        out = sub._subs_map(s.get('http://x'))
        self.assertEqual(out[0]['lang'], 'es')
        self.assertEqual(len(out), 2)
        for s in out:
            self.assertTrue(s['url'].startswith('http://127.0.0.1:9700/s/'))

    def test_cap20(self):
        s, _ = fake_session({'json': {'data': [
            {'attributes': {'language': 'es', 'files': [{'file_id': i}]}}
            for i in range(30)]}})
        self.assertEqual(len(sub._subs_map(s.get('http://x'))), 20)


class TestHandleSubtitles(unittest.TestCase):
    def test_con_key_devuelve_subs(self):
        _, undo = cfg_swap(opensubtitles_key='K')
        s, calls = fake_session({'json': {'data': [
            {'attributes': {'language': 'es', 'files': [{'file_id': 7}]}}]}})
        try:
            with patch.object(sub, '_session', return_value=s):
                d = sub.handle_subtitles('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(d['subtitles'][0]['id'], '7')
        self.assertEqual(calls[0][2]['headers']['Api-Key'], 'K')

    def test_upstream_roto_vacio(self):
        _, undo = cfg_swap(opensubtitles_key='K')
        s, _ = fake_session(RuntimeError('down'))
        try:
            with patch.object(sub, '_session', return_value=s):
                self.assertEqual(sub.handle_subtitles('movie', 'tt1'),
                                 {'subtitles': []})
        finally:
            undo()


class TestSubDownload(unittest.TestCase):
    def test_devuelve_link_cdn(self):
        _, undo = cfg_swap(opensubtitles_key='K')
        s, calls = fake_session({'json': {'link': 'https://cdn/sub.srt'}})
        try:
            with patch.object(sub, '_session', return_value=s):
                url = sub._sub_download('123')
        finally:
            undo()
        self.assertEqual(url, 'https://cdn/sub.srt')
        self.assertEqual(calls[0][2]['json'], {'file_id': 123})

    def test_post_exacto(self):
        """URL, json, headers y timeout del POST /download son los del contrato."""
        _, undo = cfg_swap(opensubtitles_key='K')
        s, calls = fake_session({'json': {'link': 'u'}})
        try:
            with patch.object(sub, '_session', return_value=s) as sess:
                sub._sub_download('123')
        finally:
            undo()
        sess.assert_called_once_with('api.opensubtitles.com')
        self.assertEqual(calls, [('POST',
            'https://api.opensubtitles.com/api/v1/download',
            {'json': {'file_id': 123},
             'headers': {'Api-Key': 'K', 'User-Agent': 'conflux 1.0'},
             'timeout': 8})])

    def test_sin_key_none(self):
        _, undo = cfg_swap(opensubtitles_key='')
        try:
            self.assertIsNone(sub._sub_download('123'))
        finally:
            undo()

    def test_error_none(self):
        _, undo = cfg_swap(opensubtitles_key='K')
        s, _ = fake_session(__import__('requests').RequestException('x'))
        try:
            with patch.object(sub, '_session', return_value=s):
                self.assertIsNone(sub._sub_download('123'))
        finally:
            undo()
