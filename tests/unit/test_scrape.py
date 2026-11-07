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
        _, undo = cfg_swap(scrapers=['yts', 'eztv', 'nyaa', 'tpb'])
        try:
            self.assertEqual(sc._scraper_names(),
                             ['YTS', 'EZTV', 'Nyaa', 'TPB'])
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
        self.assertIn('Fight Club (1999)', st0['title'])   # title_long > title (mutante Or)
        self.assertEqual(st0['behaviorHints']['seeders'], 42)
        self.assertEqual(st0['behaviorHints']['videoSize'], 2_000_000_000)
        self.assertIn('imdb_id', str(calls[0][2].get('params')))
        self.assertEqual(calls[0][2]['timeout'], 10)   # mutante 10+1
        self.assertGreaterEqual(st.HEALTH['YTS']['ok'], 1)  # mutante _health(ok->False)
        self.assertEqual(st.HEALTH['YTS']['fail'], 0)

    def test_cap_20(self):
        big = {'data': {'movie': {'title': 'T',
               'torrents': [{'hash': '%040x' % i, 'seeds': 1}
                            for i in range(21)]}}}    # 21 -> cap
        s, _ = fake_session({'json': big})
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(len(sc._sc_yts('movie', 'tt0137523')), 20)


class TestEztv(unittest.TestCase):
    def test_solo_series(self):
        self.assertEqual(sc._sc_eztv('movie', 'tt0137523'), [])

    def test_filtra_episodio(self):
        s, calls = fake_session({'json': _EZTV})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_eztv('series', 'tt0944947:1:2')
        self.assertEqual(calls[0][2]['params']['imdb_id'], '0944947')  # imdb[2:]
        self.assertEqual(calls[0][2].get('timeout'), 10)               # 10+1@87
        self.assertEqual(len(out), 1)
        self.assertIn('S01E02', out[0]['title'])
        self.assertEqual(out[0]['behaviorHints']['seeders'], 33)
        self.assertGreaterEqual(st.HEALTH['EZTV']['ok'], 1)  # mutante _health(ok->False)
        self.assertEqual(st.HEALTH['EZTV']['fail'], 0)

    def test_sin_se_no_filtra(self):
        """sid sin :S:E -> wse/wep None -> todos los torrents del imdb, sin filtrar."""
        s, _ = fake_session({'json': _EZTV})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_eztv('series', 'tt0944947')
        self.assertEqual(len(out), 2)               # S01E02 + S01E03 juntos
        self.assertGreaterEqual(st.HEALTH['EZTV']['ok'], 1)

    def test_series_imdb_invalido(self):
        """stype=series pero imdb no tt\\d+ (meta exótica) -> [] (mutante Or@84)."""
        s, _ = fake_session({'json': _EZTV})
        with patch.object(sc, '_sc_title', return_value=('k123', 'T', None)), \
                patch.object(sc, '_session', return_value=s):
            self.assertEqual(sc._sc_eztv('series', 'kitsu:123'), [])

    def test_sid_segmento_extra(self):
        """'tt:1:2:extra' -> all() solo mira parts[1:3] -> S/E filtra igual."""
        s, _ = fake_session({'json': _EZTV})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_eztv('series', 'tt0944947:1:2:foo')
        self.assertEqual(len(out), 1)
        self.assertIn('S01E02', out[0]['title'])

    def test_cap_20(self):
        big = {'torrents': [{'hash': '%040x' % i, 'filename': 'S01E%02d' % i,
                             'season': 1, 'episode': i, 'seeds': 1}
                            for i in range(1, 22)]}   # 21 -> cap
        s, _ = fake_session({'json': big})
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(len(sc._sc_eztv('series', 'tt0944947')), 20)


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
        self.assertEqual(calls[0][2]['timeout'], 10)   # mutante 10+1

    def test_item_sin_title_fallback(self):
        """item sin <title> -> 'nyaa' (Or@128: `and` daría 'nyaa' aun
        habiendo título... y `or`->`and` pierde el fallback)."""
        self._meta('Some Anime')
        xml = b"""<?xml version="1.0"?>
        <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel><item>
        <nyaa:infoHash>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</nyaa:infoHash>
        </item></channel></rss>"""
        s, _ = fake_session({'content': xml})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_nyaa('series', 'tt0944947:1:1')
        self.assertEqual(out[0]['title'], 'nyaa')
        self.assertEqual(st.HEALTH['Nyaa']['ok'], 1)      # True@130

    def test_cap_20(self):
        """out[:20] (20+1@131)."""
        self._meta('Some Anime')
        items = b''.join(
            b'<item><title>T</title>'
            b'<nyaa:infoHash>' + f'{i:040x}'.encode() + b'</nyaa:infoHash>'
            b'</item>' for i in range(21))
        xml = (b'<?xml version="1.0"?><rss xmlns:nyaa='
               b'"https://nyaa.si/xmlns/nyaa"><channel>' + items + b'</channel></rss>')
        s, _ = fake_session({'content': xml})
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(len(sc._sc_nyaa('series', 'tt0944947:1:1')), 20)

    def test_error_red_vacio(self):
        self._meta('Some Anime')
        s, _ = fake_session(Exception('down'))
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(sc._scraper('Nyaa', 'series', 'tt0944947:1:1'), [])
        self.assertGreaterEqual(st.HEALTH['Nyaa']['fail'], 1)  # False@279 vía _scraper

    def test_scraper_desconocido_vacio(self):
        self.assertEqual(sc._scraper('Nope', 'movie', 'tt1'), [])

    def test_tipo_no_soportado(self):
        """stype fuera de (series,anime,movie) -> [] aunque haya meta/rss (Or@109)."""
        s, _ = fake_session({'content': _NYAA})
        with patch.object(sc, '_sc_title',
                          return_value=('tt0944947', 'T', None)), \
                patch.object(sc, '_session', return_value=s):
            self.assertEqual(sc._sc_nyaa('channel', 'tt0944947'), [])


class TestTpb(unittest.TestCase):
    def setUp(self):
        self._old = dict(st._meta_c)
        self._h = dict(st.HEALTH)
        import time
        st._meta_c[('movie', 'tt0137523')] = (
            time.time(), {'meta': {'name': 'Fight Club'}})

    def tearDown(self):
        st._meta_c.clear(); st._meta_c.update(self._old)
        st.HEALTH.clear(); st.HEALTH.update(self._h)

    def test_items_a_streams(self):
        s, calls = fake_session({'json': [
            {'info_hash': 'a' * 40, 'name': 'Fight Club 1999 1080p',
             'seeders': 42, 'size': '2000000000', 'imdb': 'tt0137523'},
            {'info_hash': 'b' * 40, 'name': 'Otro', 'seeders': 9,
             'size': '9', 'imdb': 'tt9999999'},    # imdb disidente -> fuera
            {'name': 'sin hash'},                  # sin info_hash -> fuera
        ]})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_tpb('movie', 'tt0137523')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'TPB')
        self.assertEqual(out[0]['infoHash'], 'a' * 40)
        self.assertEqual(out[0]['behaviorHints']['seeders'], 42)
        self.assertEqual(st.HEALTH['TPB']['ok'], 1)    # mutante _health(ok->False)
        self.assertEqual(st.HEALTH['TPB']['fail'], 0)
        self.assertEqual(st.HEALTH['TPB']['err'], '')
        self.assertIn('Fight Club', calls[0][2]['params']['q'])
        self.assertEqual(calls[0][2]['params']['cat'], '200')
        self.assertEqual(calls[0][2]['timeout'], 10)         # 10+1@146

    def test_name_faltante_tpb(self):
        """t.get('name') or 'tpb' (Or@155) + cap out[:20] (20+1@158)."""
        s, _ = fake_session({'json': [
            {'info_hash': f'{i:040x}', 'seeders': 1, 'size': '1'}
            for i in range(21)] + [{'info_hash': 'd' * 40, 'seeders': 1}]})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_tpb('movie', 'tt0137523')
        self.assertEqual(len(out), 20)                       # cap
        self.assertEqual(out[0]['title'].split(' 👤')[0], 'tpb')

    def test_dict_error_no_lista(self):
        s, _ = fake_session({'json': {'error': 'no results'}})
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(sc._sc_tpb('movie', 'tt0137523'), [])

    def test_series_query_sxxeyy(self):
        import time
        st._meta_c[('series', 'tt0944947')] = (
            time.time(), {'meta': {'name': 'Some Show'}})
        s, calls = fake_session({'json': []})
        with patch.object(sc, '_session', return_value=s):
            sc._sc_tpb('series', 'tt0944947:2:3')
        self.assertIn('s02e03', calls[0][2]['params']['q'])

    def test_tipo_y_sin_meta_vacio(self):
        self.assertEqual(sc._sc_tpb('other', 'tt0137523'), [])
        st._meta_c.clear()
        self.assertEqual(sc._sc_tpb('movie', 'tt0137523'), [])

    def test_error_red_vacio(self):
        s, _ = fake_session(Exception('down'))
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(sc._scraper('TPB', 'movie', 'tt0137523'), [])


class TestNyaaSize(unittest.TestCase):
    def test_unidades(self):
        self.assertEqual(sc._nyaa_size('1.5 GiB'), int(1.5 * (1 << 30)))
        self.assertEqual(sc._nyaa_size('700 MiB'), 700 * (1 << 20))
        self.assertEqual(sc._nyaa_size('basura'), 0)
        self.assertEqual(sc._nyaa_size(''), 0)
        self.assertEqual(sc._nyaa_size('512 KiB'), 512 * (1 << 10))  # 10+1
        self.assertEqual(sc._nyaa_size('2 TiB'), 2 * (1 << 40))      # 40+1


_RSS = b"""<?xml version="1.0"?>
<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"
 xmlns:nyaa="https://nyaa.si/xmlns/nyaa">
<channel>
 <item>
  <title>Show.S01E01.1080p.WEB</title>
  <link>magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA&amp;dn=x</link>
  <enclosure url="https://feed.example/dl/1.torrent" length="700000000"/>
  <torznab:attr name="seeders" value="55"/>
 </item>
 <item>
  <title>Movie.2160p</title>
  <link>https://feed.example/detail/2</link>
  <torznab:attr name="infohash" value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"/>
  <torznab:attr name="size" value="3000000000"/>
  <torznab:attr name="peers" value="4"/>
 </item>
 <item>
  <title>Anime - 03</title>
  <link>https://feed.example/dl/3.torrent</link>
  <nyaa:infoHash>CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC</nyaa:infoHash>
  <nyaa:size>800 MiB</nyaa:size>
 </item>
 <item>
  <title>Sin magnet ni hash</title>
  <link>https://feed.example/detail/4</link>
 </item>
</channel></rss>"""


class TestRss(unittest.TestCase):
    """Scraper RSS genérico — feeds de indexers vía `scraper_rss` (gap 4)."""

    def setUp(self):
        # _public_url_cached consulta DNS vía net (en tests stub no-DNS); aquí
        # se permite todo para aislar el map — el guard se prueba aparte.
        self._p = patch.object(sc, '_public_url_cached', lambda u: True)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_map_nombres_por_host(self):
        _, undo = cfg_swap(scraper_rss=[
            'https://showrss.info/allshows.rss', 'nope-not-url',
            'http://feed2.lan/rss.xml'])
        try:
            self.assertEqual(sc._rss_map(), {
                'RSS:showrss.info': 'https://showrss.info/allshows.rss',
                'RSS:feed2.lan': 'http://feed2.lan/rss.xml'})
        finally:
            undo()

    def test_map_sin_config(self):
        _, undo = cfg_swap(scraper_rss=[], scrapers=[])
        try:
            self.assertEqual(sc._rss_map(), {})
            self.assertEqual(sc._scraper_names(), [])
        finally:
            undo()

    def test_items_a_streams(self):
        s, calls = fake_session({'content': _RSS})
        with patch.object(sc, '_session') as msess:
            msess.return_value = s
            out = sc._sc_rss('https://feed.example/rss', 'movie', 'tt0137523')
        msess.assert_called_once_with('feed.example')   # sesión por host del feed
        self.assertEqual(calls[0][1], 'https://feed.example/rss')
        self.assertEqual(len(out), 3)                   # el sin-hash filtrado
        by_title = {x['title']: x for x in out}
        s1 = by_title['Show.S01E01.1080p.WEB']
        self.assertEqual(s1['name'], 'RSS:feed.example')
        self.assertEqual(s1['infoHash'], 'a' * 40)
        self.assertEqual(s1['behaviorHints']['seeders'], 55)
        self.assertEqual(s1['behaviorHints']['videoSize'], 700000000)
        s2 = by_title['Movie.2160p']                    # attr infohash (sin magnet)
        self.assertEqual(s2['infoHash'], 'b' * 40)
        self.assertEqual(s2['behaviorHints']['videoSize'], 3000000000)
        s3 = by_title['Anime - 03']                     # <ns:infoHash> elemento
        self.assertEqual(s3['infoHash'], 'c' * 40)
        self.assertEqual(s3['behaviorHints']['videoSize'], 800 * (1 << 20))
        self.assertEqual(calls[0][2]['timeout'], 10)      # 10+1@242
        self.assertGreaterEqual(st.HEALTH['RSS:feed.example']['ok'], 1)  # True@248

    def test_hash_fuentes_alternativas(self):
        """infoHash en attr con H mayúscula (Or@199), magnet solo en <guid>
        y solo en attr magneturl (Or@201): cada fuente es un `or` aparte."""
        xml = b"""<?xml version="1.0"?>
        <rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>
         <item><title>A</title>
          <torznab:attr name="infoHash" value="dddddddddddddddddddddddddddddddddddddddd"/>
         </item>
         <item><title>B</title>
          <guid>magnet:?xt=urn:btih:EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE&amp;dn=x</guid>
         </item>
         <item><title>C</title>
          <torznab:attr name="magneturl" value="magnet:?xt=urn:btih:FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"/>
         </item>
         <item><title>D</title>
          <torznab:attr name="magnet" value="magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567"/>
         </item>
        </channel></rss>"""
        s, _ = fake_session({'content': xml})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_rss('https://feed.example/rss', 'movie', 'tt1')
        self.assertEqual({x['infoHash'] for x in out},
                         {'d' * 40, 'e' * 40, 'f' * 40,
                          '0123456789abcdef0123456789abcdef01234567'})

    def test_title_faltante_rss_y_cap(self):
        """title -> 'rss' (Or@239) y out[:20] (20+1@249)."""
        items = b''.join(
            b'<item><link>magnet:?xt=urn:btih:' + f'{i:040x}'.encode()
            + b'</link></item>' for i in range(21))
        xml = b'<?xml version="1.0"?><rss><channel>' + items + b'</channel></rss>'
        s, _ = fake_session({'content': xml})
        with patch.object(sc, '_session', return_value=s):
            out = sc._sc_rss('https://feed.example/rss', 'movie', 'tt1')
        self.assertEqual(len(out), 20)
        self.assertEqual(out[0]['title'], 'rss')

    def test_scraper_names_incluye_feeds(self):
        _, undo = cfg_swap(scrapers=[],
                           scraper_rss=['https://feed.example/rss'])
        try:
            self.assertEqual(sc._scraper_names(), ['RSS:feed.example'])
        finally:
            undo()

    def test_scraper_rutea_y_aisla_error(self):
        s, _ = fake_session({'content': _RSS})
        _, undo = cfg_swap(scraper_rss=['https://feed.example/rss'])
        try:
            with patch.object(sc, '_session', return_value=s):
                out = sc._scraper('RSS:feed.example', 'movie', 'tt1')
            self.assertEqual(len(out), 3)
        finally:
            undo()
        s2, _ = fake_session(Exception('down'))
        _, undo = cfg_swap(scraper_rss=['https://feed.example/rss'])
        try:
            with patch.object(sc, '_session', return_value=s2):
                self.assertEqual(
                    sc._scraper('RSS:feed.example', 'movie', 'tt1'), [])
        finally:
            undo()

    def test_sin_items_vacio(self):
        s, _ = fake_session({'content': b'<rss><channel></channel></rss>'})
        with patch.object(sc, '_session', return_value=s):
            self.assertEqual(sc._sc_rss('https://f/x', 'movie', 'tt1'), [])

    def test_ssrf_linklocal_filtrado(self):
        """scraper_rss pasaba por _session sin validar: un feed apuntando a
        169.254.169.254 (metadata cloud) se fetcheaba. Ahora pasa _public_url
        como epg_url — en tests el stub no-DNS bloquea la IP literal."""
        self._p.stop()                               # usamos el guard real
        try:
            _, undo = cfg_swap(scraper_rss=[
                'http://169.254.169.254/latest/meta-data',
                'https://feed.example/rss'])
            try:
                m = sc._rss_map()
            finally:
                undo()
        finally:
            self._p.start()
        self.assertEqual(m, {'RSS:feed.example': 'https://feed.example/rss'})

    def test_public_url_cached_memo(self):
        """_public_url_cached memoiza: la 2ª llamada no repite DNS
        (ruta caliente /stream)."""
        import conflux.infra.net as net
        net._PUBLIC_URL_OK.clear()
        with patch.object(net, '_public_url', return_value=True) as pu:
            self.assertTrue(net._public_url_cached('https://a.example/f'))
            self.assertTrue(net._public_url_cached('https://a.example/f'))
        self.assertEqual(pu.call_count, 1)

    def test_tipo_no_soportado_no_pide(self):
        """stype fuera de movie/series/anime -> [] SIN fetch (Or@239:
        `and` dejaría pasar el gate y pediría al feed)."""
        s, _ = fake_session({'content': _RSS})
        with patch.object(sc, '_session', return_value=s) as msess:
            self.assertEqual(sc._sc_rss('https://f/x', 'channel', 'tt1'), [])
        msess.assert_not_called()

    def test_sin_sid_no_pide(self):
        """sid falsy -> [] SIN fetch (segundo or del mismo gate)."""
        s, _ = fake_session({'content': _RSS})
        with patch.object(sc, '_session', return_value=s) as msess:
            self.assertEqual(sc._sc_rss('https://f/x', 'movie', ''), [])
        msess.assert_not_called()


if __name__ == '__main__':
    unittest.main()
