"""Unit tests conflux.app.subs — OpenSubtitles."""
import unittest
from unittest.mock import MagicMock, patch

import conflux.app.subs as sub
from conflux.core import cfg


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
