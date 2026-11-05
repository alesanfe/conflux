# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.torznab — parseo Torznab XML (defusedxml)."""
import unittest
from unittest.mock import MagicMock, patch

import conflux.core.state as st
import conflux.infra.torznab as tz
from tests import cfg_swap, fake_session


class TestTzItem(unittest.TestCase):
    def _item(self, xml):
        return tz._ET.fromstring(xml)

    def test_magnet_link(self):
        it = self._item(
            '<item><title>T</title><link>magnet:?xt=urn:btih:'
            'abcdef0123456789abcdef0123456789abcdef01</link></item>')
        s = tz._tz_item(it, {})
        self.assertEqual(s['infoHash'], 'abcdef0123456789abcdef0123456789abcdef01')
        self.assertEqual(s['name'], 'Torznab')

    def test_sin_magnet_none(self):
        it = self._item('<item><title>T</title><link>http://x/dl</link></item>')
        self.assertIsNone(tz._tz_item(it, {}))

    def test_seeders_desde_attrs(self):
        it = self._item(
            '<item><title>T</title>'
            '<link>magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01</link></item>')
        s = tz._tz_item(it, {'seeders': '42', 'size': '1024'})
        self.assertEqual(s['behaviorHints']['seeders'], 42)


class TestTorznab(unittest.TestCase):
    def setUp(self):
        self._old = dict(st.CFG)

    def tearDown(self):
        st.CFG.clear(); st.CFG.update(self._old)

    def test_sin_config_devuelve_vacio(self):
        st.CFG.pop('torznab_url', None); st.CFG.pop('torznab_key', None)
        self.assertEqual(tz._torznab('movie', 'tt0137523'), [])

    def test_url_vacia_no_fetcha_aunque_haya_key(self):
        """or-chain: cada guarda corta por sí sola (Or@19: and requeriría todas)."""
        st.CFG['torznab_url'] = ''
        st.CFG['torznab_key'] = 'k'
        with patch.object(tz, '_tz_query') as q:
            self.assertEqual(tz._torznab('movie', 'tt0137523'), [])
            q.assert_not_called()
        st.CFG['torznab_url'] = 'http://x'
        st.CFG['torznab_key'] = ''
        with patch.object(tz, '_tz_query') as q:
            self.assertEqual(tz._torznab('movie', 'tt0137523'), [])
            q.assert_not_called()

    def test_no_tt_devuelve_vacio(self):
        st.CFG['torznab_url'] = 'http://x'
        st.CFG['torznab_key'] = 'k'
        self.assertEqual(tz._torznab('movie', 'kitsu:1'), [])

    def test_movie_t_movie(self):
        st.CFG['torznab_url'] = 'http://x'
        st.CFG['torznab_key'] = 'k'
        seen = {}
        class S(MagicMock):
            def get(self, url, params=None, timeout=None):
                seen['t'] = params['t']
                r = MagicMock(); r.content = b'<rss/>'; return r
        with patch.object(tz, '_session', return_value=S()):
            tz._torznab('movie', 'tt0137523')
            self.assertEqual(seen['t'], 'movie')
            tz._torznab('series', 'tt0137523:1:1')
            self.assertEqual(seen['t'], 'tvsearch')


if __name__ == '__main__':
    unittest.main()



class TestTorznabErrorPath(unittest.TestCase):
    def test_excepcion_marca_health_y_vacio(self):
        _, undo = cfg_swap(torznab_url='http://idx', torznab_key='K')
        try:
            with patch.object(tz, '_tz_query', side_effect=RuntimeError('boom')):
                out = tz._torznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(out, [])
        self.assertEqual(st.HEALTH['Torznab']['fail'], 1)


class TestTzQuery(unittest.TestCase):
    def test_parsea_items_xml(self):
        xml = ('<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>'
               '<item><title>Rel</title>'
               '<link>magnet:?xt=urn:btih:abcdef0123456789abcdef0123456789abcdef01</link>'
               '<torznab:attr name="seeders" value="7"/></item>'
               '</channel></rss>')
        s2, _ = fake_session({'content': xml.encode()})
        with patch.object(tz, '_session', return_value=s2):
            out = tz._tz_query('http://idx', 'K', 'movie', 'tt1')
        self.assertEqual(out[0]['title'], 'Rel')
        self.assertEqual(out[0]['infoHash'], 'abcdef0123456789abcdef0123456789abcdef01')
        self.assertEqual(out[0]['behaviorHints']['seeders'], 7)






class TestProwlarrIds(unittest.TestCase):
    """_tz_prowlarr_ids: caché 600s + endpoint indexers."""

    def setUp(self):
        self._old = dict(tz._TZ_IDS)
        tz._TZ_IDS.update(ts=0, ids=None)

    def tearDown(self):
        tz._TZ_IDS.clear(); tz._TZ_IDS.update(self._old)

    def test_cache_fresca_no_llama(self):
        tz._TZ_IDS.update(ts=tz.CLOCK.time(), ids=[1, 2])
        with patch.object(tz, '_session') as ses:
            self.assertEqual(tz._tz_prowlarr_ids('http://p', 'k'), [1, 2])
            ses.assert_not_called()

    def test_respuesta_ok_ids_habilitados(self):
        r = MagicMock(ok=True)
        r.json.return_value = [{'id': 5, 'enable': True},
                               {'id': 9, 'enable': False}]
        s2 = MagicMock()
        s2.get.return_value = r
        with patch.object(tz, '_session', return_value=s2):
            self.assertEqual(tz._tz_prowlarr_ids('http://p', 'k'), [5])

    def test_error_devuelve_none(self):
        s2 = MagicMock()
        s2.get.side_effect = OSError('down')
        with patch.object(tz, '_session', return_value=s2):
            self.assertIsNone(tz._tz_prowlarr_ids('http://p', 'k'))

    def test_cache_borde_600s(self):
        """now-ts == 600 -> stale, refetch (Lt@33: <= creería fresco; 600+1@33)."""
        from freezegun import freeze_time
        with freeze_time('2025-06-01 12:00:00'):
            tz._TZ_IDS.update(ts=tz.CLOCK.time() - 600, ids=[1])
        r = MagicMock(ok=True)
        r.json.return_value = [{'id': 2}]
        s2 = MagicMock(); s2.get.return_value = r
        with freeze_time('2025-06-01 12:00:00'), \
                patch.object(tz, '_session', return_value=s2):
            self.assertEqual(tz._tz_prowlarr_ids('http://p', 'k'), [2])
        s2.get.assert_called_once()

    def test_cache_casi_vieja_sirve(self):
        """now-ts = 599 -> cache hit (600+1@33: 601 lo creería viejo)."""
        tz._TZ_IDS.update(ts=tz.CLOCK.time() - 599, ids=[7])
        with patch.object(tz, '_session') as ses:
            self.assertEqual(tz._tz_prowlarr_ids('http://p', 'k'), [7])
            ses.assert_not_called()

    def test_sin_enable_cuenta_habilitado(self):
        """i.get('enable', True) — clave ausente = habilitado (True@39)."""
        r = MagicMock(ok=True)
        r.json.return_value = [{'id': 5}, {'id': 9, 'enable': False}]
        s2 = MagicMock(); s2.get.return_value = r
        with patch.object(tz, '_session', return_value=s2):
            self.assertEqual(tz._tz_prowlarr_ids('http://p', 'k'), [5])

    def test_timeout_indexers(self):
        s2 = MagicMock(); s2.get.return_value = MagicMock(ok=False)
        with patch.object(tz, '_session', return_value=s2):
            tz._tz_prowlarr_ids('http://p', 'k')
        self.assertEqual(s2.get.call_args[1]['timeout'], 8)


class TestProwlarrExpand(unittest.TestCase):
    """_tz_query con URL /0/api: expande a cada indexer habilitado."""

    def test_expande_indexers(self):
        urls = []
        class S(MagicMock):
            def get(self, url, params=None, timeout=None):
                urls.append(url)
                r = MagicMock(); r.content = b'<rss/>'; return r
        with patch.object(tz, '_tz_prowlarr_ids', return_value=[3, 7]), \
                patch.object(tz, '_session', return_value=S()):
            tz._tz_query('http://p/0/api', 'k', 'movie', 'tt1')
        self.assertEqual(sorted(urls), ['http://p/3/api', 'http://p/7/api'])

    def test_dedup_infohash(self):
        """s and hash not in seen — duplicado entre indexers no duplica (And@78)."""
        ih = 'abcdef0123456789abcdef0123456789abcdef01'
        xml = (f'<rss><channel><item><title>A</title><link>magnet:?xt=urn:btih:{ih}</link></item>'
               f'<item><title>B</title><link>magnet:?xt=urn:btih:{ih}</link></item>'
               '</channel></rss>')
        s2, _ = fake_session({'content': xml.encode()})
        with patch.object(tz, '_session', return_value=s2):
            out = tz._tz_query('http://idx', 'K', 'movie', 'tt1')
        self.assertEqual(len(out), 1)

    def test_get_timeout_10(self):
        """_one usa timeout=10 por petición (10+1@69)."""
        s2 = MagicMock()
        r = MagicMock(); r.content = b'<rss/>'
        s2.get.return_value = r
        with patch.object(tz, '_session', return_value=s2):
            tz._tz_query('http://idx', 'K', 'movie', 'tt1')
        self.assertEqual(s2.get.call_args[1]['timeout'], 10)

    def test_futuro_timeout_15(self):
        """f.result(timeout=15) por indexer (15+1@77)."""
        seen_t = []
        class F:
            def result(self, timeout=None):
                seen_t.append(timeout)
                return []
        with patch.object(tz._POOL, 'submit', return_value=F()):
            tz._tz_query('http://idx', 'K', 'movie', 'tt1')
        self.assertEqual(seen_t, [15])

    def test_sin_indexers_url_literal(self):
        urls = []
        class S(MagicMock):
            def get(self, url, params=None, timeout=None):
                urls.append(url)
                r = MagicMock(); r.content = b'<rss/>'; return r
        with patch.object(tz, '_tz_prowlarr_ids', return_value=None), \
                patch.object(tz, '_session', return_value=S()):
            tz._tz_query('http://p/0/api', 'k', 'movie', 'tt1')
        self.assertEqual(urls, ['http://p/0/api'])
