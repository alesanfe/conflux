# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.torznab — parseo Torznab XML (defusedxml)."""
import unittest
from unittest.mock import MagicMock, patch

import conflux.core.state as st
import conflux.infra.torznab as tz


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
