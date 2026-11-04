# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.newznab — API Newznab -> streams Usenet /nzb/."""
import base64
import unittest
from unittest.mock import patch

import conflux.infra.newznab as nz
from tests import cfg_swap, fake_session

_XML = b"""<?xml version="1.0"?><rss
 xmlns:newznab="http://www.newznab.org/DTD/2010/feeds/attributes/">
 <channel><item>
   <title>Movie.1080p</title>
   <enclosure url="https://nz.example/get/x.nzb" length="1500"/>
   <newznab:attr name="size" value="2000"/>
 </item><item>
   <title>Sin enclosure</title>
 </item></channel></rss>"""


class TestNum(unittest.TestCase):
    def test_int_y_coma_decimal(self):
        self.assertEqual(nz._num('12,5'), 12)   # '12,5' -> 12.5 -> 12
        self.assertEqual(nz._num(7), 7)

    def test_basura_cero(self):
        self.assertEqual(nz._num('abc'), 0)
        self.assertEqual(nz._num(None), 0)
        self.assertEqual(nz._num(''), 0)


class TestNewznab(unittest.TestCase):
    CFG = dict(newznab_url='http://nz.lan/api', newznab_key='k',
               debrid_provider='torbox', debrid_key='dk')

    def _run(self, stype='movie', sid='tt0137523', extra=None):
        s, calls = fake_session({'content': _XML})
        _, undo = cfg_swap(**{**self.CFG, **(extra or {})})
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab(stype, sid)
        finally:
            undo()
        return out, calls

    def test_sin_config_vacio(self):
        _, undo = cfg_swap(newznab_url='', newznab_key='')
        try:
            self.assertEqual(nz._newznab('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_sin_torbox_vacio(self):
        """Usenet solo se emite con debrid torbox (los NZB se resuelven ahí)."""
        _, undo = cfg_swap(**{**self.CFG, 'debrid_provider': 'real-debrid'})
        try:
            self.assertEqual(nz._newznab('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_params_movie_vs_series(self):
        _, calls = self._run('movie', 'tt0137523')
        self.assertEqual(calls[0][2]['params']['t'], 'movie')
        _, calls = self._run('series', 'tt0944947:1:2')
        p = calls[0][2]['params']
        self.assertEqual(p['t'], 'tvsearch')
        self.assertEqual((p['season'], p['ep']), ('1', '2'))

    def test_items_a_streams(self):
        out, _ = self._run()
        self.assertEqual(len(out), 1)          # item sin enclosure filtrado
        s = out[0]
        self.assertEqual(s['name'], 'Usenet')
        self.assertIn('/nzb/', s['url'])
        link = base64.urlsafe_b64decode(s['url'].rsplit('/', 1)[-1] + '==').decode()
        self.assertEqual(link, 'https://nz.example/get/x.nzb')
        self.assertEqual(s['behaviorHints']['videoSize'], 2000)

    def test_error_red_vacio(self):
        s, _ = fake_session(Exception('down'))
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                self.assertEqual(nz._newznab('movie', 'tt0137523'), [])
        finally:
            undo()


class TestNntp(unittest.TestCase):
    """NNTP nativo (nzbUrl+servers) — Stremio Desktop v5 reproduce sin backend."""
    CFG = dict(newznab_url='http://nz.lan/api', newznab_key='k',
               nntp_host='news.ej.com', nntp_user='u', nntp_pass='p',
               nntp_port=563, nntp_ssl=True, nntp_connections=5)

    def test_server_url(self):
        _, undo = cfg_swap(**self.CFG)
        try:
            self.assertEqual(nz._nntp_server(),
                             'nntps://u:p@news.ej.com:563/5')
        finally:
            undo()

    def test_server_sin_ssl_y_cred_escapadas(self):
        _, undo = cfg_swap(**{**self.CFG, 'nntp_ssl': False,
                              'nntp_user': 'u@x', 'nntp_pass': 'p w'})
        try:
            self.assertEqual(nz._nntp_server(),
                             'nntp://u%40x:p%20w@news.ej.com:563/5')
        finally:
            undo()

    def test_server_incompleto_none(self):
        for miss in ('nntp_host', 'nntp_user', 'nntp_pass'):
            _, undo = cfg_swap(**{**self.CFG, miss: ''})
            try:
                self.assertIsNone(nz._nntp_server(), miss)
            finally:
                undo()

    def test_nntp_solo_sin_torbox(self):
        """Sin debrid pero con NNTP: emite la variante nativa (nzbUrl)."""
        s, _ = fake_session({'content': _XML})
        _, undo = cfg_swap(**{**self.CFG, 'debrid_provider': '', 'debrid_key': '',
                              'debrid_providers': {}})
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'Usenet SN')
        self.assertEqual(out[0]['nzbUrl'], 'https://nz.example/get/x.nzb')
        self.assertEqual(out[0]['servers'], ['nntps://u:p@news.ej.com:563/5'])
        self.assertIn('.mkv', out[0]['fileMustInclude'])
        self.assertNotIn('url', out[0])                    # no toca /nzb/

    def test_dual_torbox_y_nntp(self):
        """TorBox + NNTP: dos variantes del mismo item."""
        s, _ = fake_session({'content': _XML})
        _, undo = cfg_swap(**{**self.CFG, 'debrid_provider': 'torbox',
                              'debrid_key': 'dk'})
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(out), 2)
        names = {x['name'] for x in out}
        self.assertEqual(names, {'Usenet', 'Usenet SN'})

    def test_multi_debrid_torbox_segundo(self):
        """TorBox como provider extra también habilita la vía /nzb/."""
        s, _ = fake_session({'content': _XML})
        _, undo = cfg_swap(**{**self.CFG, 'nntp_host': '',
                              'debrid_provider': 'real-debrid', 'debrid_key': 'k',
                              'debrid_providers': {'torbox': 'tk'}})
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'Usenet')


if __name__ == '__main__':
    unittest.main()
