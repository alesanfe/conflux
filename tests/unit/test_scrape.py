# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.scrape — scrapers integrados (yts/eztv/nyaa)."""
import unittest
from unittest.mock import patch

import conflux.core.state as st
import conflux.infra.scrape as sc
from tests import cfg_swap, fake_session

_YTS = {'data': {'movie': {'title': 'Fight Club', 'title_long': 'Fight Club (1999)',
        'torrents': [{'hash': 'A' * 40, 'quality': '1080p', 'type': 'bluray',
                      'seeds': 42, 'size_bytes': 2_000_000_000},
                     {'quality': '720p'}]}}}           # el 2º sin hash -> fuera

_EZTV = {'torrents': [{'hash': 'b' * 40, 'filename': 'Show.S01E02.1080p',
                     'season': '1', 'episode': '2', 'seeds': 33,
                     'size_bytes': 500_000_000},
                    {'hash': 'c' * 40, 'filename': 'Show.S01E03.1080p',
                     'season': 1, 'episode': 3, 'seeds': 9}]}

_NYAA = b"""<?xml version="1.0"?>
<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa">
<channel><item>
  <title>Anime - 01</title>
  <nyaa:infoHash>D4D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5</nyaa:infoHash>
  <nyaa:seeders>17</nyaa:seeders>
  <nyaa:size>1.4 GiB</nyaa:size>
</item><item><title>sin hash</title></item></channel></rss>"""


class TestEnabled(unittest.TestCase):
    def test_default_todos(self):
        _, undo = cfg_swap(scrapers=['yts', 'eztv', 'nyaa'])
        try:
            self.assertEqual(sc._scraper_names(), ['YTS', 'EZTV', 'Nyaa'])
        finally:
            undo()

    def test_subset_y_desconocido(self):
        _, undo = cfg_swap(scrapers=['yts', 'zzz'])
        try:
            self.assertEqual(sc._scraper_names(), ['YTS'])
        finally:
            undo()

    def test_vacio_off(self):
        _, undo = cfg_swap(scrapers=[])
        try:
            self.assertEqual(sc._scraper_names(), [])
        finally:
            undo()


class TestYts(unittest.TestCase):
    def test_solo_movies_tt(self):
        self.assertEqual(sc._sc_yts('series', 'tt0137523:1:1'), [])
        self.assertEqual(sc._sc_yts('movie', 'kitsu:1'), [])

    def test_torrents_a_streams(self):
        s, calls = fake_session({'json': _YTS})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_yts('movie', 'tt0137523')
        self.assertEqual(len(out), 1)               # el sin-hash filtrado
        st0 = out[0]
        self.assertEqual(st0['name'], 'YTS')
        self.assertEqual(st0['infoHash'], 'a' * 40)  # minúsculas
        self.assertEqual(st0['behaviorHints']['seeders'], 42)
        self.assertEqual(st0['behaviorHints']['videoSize'], 2_000_000_000)
        self.assertIn('imdb_id', str(calls[0][2].get('params')))


class TestEztv(unittest.TestCase):
    def test_solo_series(self):
        self.assertEqual(sc._sc_eztv('movie', 'tt0137523'), [])

    def test_filtra_episodio(self):
        s, _ = fake_session({'json': _EZTV})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_eztv('series', 'tt0944947:1:2')
        self.assertEqual(len(out), 1)
        self.assertIn('S01E02', out[0]['title'])
        self.assertEqual(out[0]['behaviorHints']['seeders'], 33)


class TestNyaa(unittest.TestCase):
    def _meta(self, title):
        import time
        st._meta_c[('series', 'tt0944947')] = (time.time(), {'meta': {'name': title}})

    def setUp(self):
        self._old = dict(st._meta_c)
        self._h = dict(st.HEALTH)

    def tearDown(self):
        st._meta_c.clear(); st._meta_c.update(self._old)
        st.HEALTH.clear(); st.HEALTH.update(self._h)

    def test_sin_meta_vacio(self):
        self.assertEqual(sc._sc_nyaa('series', 'tt0944947:1:1'), [])

    def test_items_a_streams(self):
        self._meta('Some Anime')
        s, calls = fake_session({'content': _NYAA})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_nyaa('series', 'tt0944947:1:1')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'Nyaa')
        self.assertEqual(out[0]['infoHash'],
                         'D4D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5D5'.lower())
        self.assertEqual(out[0]['behaviorHints']['seeders'], 17)
        self.assertEqual(out[0]['behaviorHints']['videoSize'],
                         int(1.4 * (1 << 30)))
        self.assertIn('s01e01', calls[0][2]['params']['q'])

    def test_error_red_vacio(self):
        self._meta('Some Anime')
        s, _ = fake_session(Exception('down'))
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(sc._scraper('Nyaa', 'series', 'tt0944947:1:1'), [])

    def test_scraper_desconocido_vacio(self):
        self.assertEqual(sc._scraper('Nope', 'movie', 'tt1'), [])


class TestNyaaSize(unittest.TestCase):
    def test_unidades(self):
        self.assertEqual(sc._nyaa_size('1.5 GiB'), int(1.5 * (1 << 30)))
        self.assertEqual(sc._nyaa_size('700 MiB'), 700 * (1 << 20))
        self.assertEqual(sc._nyaa_size('basura'), 0)
        self.assertEqual(sc._nyaa_size(''), 0)


if __name__ == '__main__':
    unittest.main()
