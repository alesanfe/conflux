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


class TestHandleSubs(unittest.TestCase):
    def test_sin_key_vacio(self):
        with patch.object(cfg, '_cfgget', return_value=None):
            self.assertEqual(sub.handle_subtitles('movie', 'tt1'), {'subtitles': []})


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
