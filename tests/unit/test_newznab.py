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

    def test_url_vacia_no_pide(self):
        """newznab_url='' pero key+via ok -> [] sin llamar a _session
        (mutante Or@90 enmascarado por el except de red)."""
        s, _ = fake_session({'content': _XML})
        _, undo = cfg_swap(**{**self.CFG, 'newznab_url': ''})
        try:
            with patch.object(nz, '_session', return_value=s) as m:
                self.assertEqual(nz._newznab('movie', 'tt0137523'), [])
            m.assert_not_called()
        finally:
            undo()

    def test_movie_con_se_no_manda_season(self):
        """stype=movie no añade season/ep aunque el sid traiga :S:E (And@96)."""
        _, calls = self._run('movie', 'tt0137523:1:2')
        self.assertNotIn('season', calls[0][2]['params'])
        self.assertNotIn('ep', calls[0][2]['params'])

    def test_sin_torbox_vacio(self):
        """Usenet solo se emite con debrid torbox (los NZB se resuelven ahí)."""
        _, undo = cfg_swap(**{**self.CFG, 'debrid_provider': 'real-debrid'})
        try:
            self.assertEqual(nz._newznab('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_ssrf_linklocal_no_pide(self):
        """newznab_url a IP link-local se rechaza con _public_url (= epg_url)."""
        s, _ = fake_session({'content': _XML})
        _, undo = cfg_swap(**{**self.CFG,
                              'newznab_url': 'http://169.254.169.254/api'})
        try:
            with patch.object(nz, '_session', return_value=s) as m:
                self.assertEqual(nz._newznab('movie', 'tt0137523'), [])
            m.assert_not_called()
        finally:
            undo()

    def test_sab_ssrf_linklocal_none(self):
        """_sab_cfg con sabnzbd_url link-local -> None (no addurl al metadata)."""
        _, undo = cfg_swap(sabnzbd_url='http://169.254.169.254:8080',
                           sabnzbd_key='k')
        try:
            self.assertIsNone(nz._sab_cfg())
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
        self.assertEqual(s['title'], 'Movie.1080p')  # title real, no 'usenet' (Or@111)
        self.assertIn('/nzb/', s['url'])
        link = base64.urlsafe_b64decode(s['url'].rsplit('/', 1)[-1] + '==').decode()
        self.assertEqual(link, 'https://nz.example/get/x.nzb')
        self.assertEqual(s['behaviorHints']['videoSize'], 2000)

    def test_size_de_enclosure_length(self):
        """Sin attr size: sz cae a enclosure@length (mutante IsNot@112)."""
        xml = b"""<?xml version="1.0"?><rss><channel><item>
          <title>X</title>
          <enclosure url="https://nz.example/y.nzb" length="1500"/>
        </item></channel></rss>"""
        s, _ = fake_session({'content': xml})
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(out[0]['behaviorHints']['videoSize'], 1500)

    def test_error_red_vacio(self):
        import conflux.core.state as st
        fails = st.HEALTH.get('Newznab', {}).get('fail', 0)
        s, _ = fake_session(Exception('down'))
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                self.assertEqual(nz._newznab('movie', 'tt0137523'), [])
        finally:
            undo()
        # _health('Newznab', False,...) — mutante False->True@153 lo inflaría 'ok'
        self.assertEqual(st.HEALTH['Newznab']['fail'], fails + 1)

    def test_timeout_10_y_health_ok(self):
        """GET al indexer con timeout=10 (10+1@147) y _health(True) al ok
        (mutante True->False@150 lo mandaría a 'fail')."""
        import conflux.core.state as st
        oks = st.HEALTH.get('Newznab', {}).get('ok', 0)
        _, calls = self._run()
        self.assertEqual(calls[0][2].get('timeout'), 10)
        self.assertEqual(st.HEALTH['Newznab']['ok'], oks + 1)

    def test_cap_30_streams(self):
        """out[:30] — 31 items -> 30 streams exactos (mutante 30+1@151)."""
        items = ''.join(
            f'<item><title>T{i}</title>'
            f'<enclosure url="https://nz.example/{i}.nzb" length="1"/></item>'
            for i in range(31))
        xml = f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>'.encode()
        s, _ = fake_session({'content': xml})
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(out), 30)


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

    def test_defaults_port_ssl_conns(self):
        """Sin nntp_port/ssl/connections -> 563, nntps, 5 conns (563+1, 5+1).
        cfg_swap hace MERGE: hay que hacer pop de las claves reales para
        ejercitar el branch del default (True@46 / 563+1@47)."""
        import conflux.core.state as st
        _, undo = cfg_swap(newznab_url='', newznab_key='',
                           nntp_host='h', nntp_user='u', nntp_pass='p')
        try:
            for k in ('nntp_port', 'nntp_ssl', 'nntp_connections'):
                st.CFG.pop(k, None)
            self.assertEqual(nz._nntp_server(), 'nntps://u:p@h:563/5')
        finally:
            undo()

    def test_port_y_conns_invalidos_default(self):
        """port/conns no numéricos -> 563/5 (fallbacks del int())."""
        _, undo = cfg_swap(**{**self.CFG, 'nntp_port': 'abc',
                              'nntp_connections': 'xyz'})
        try:
            self.assertEqual(nz._nntp_server(),
                             'nntps://u:p@news.ej.com:563/5')
        finally:
            undo()

    def test_conns_cero_clamp_a_uno(self):
        """conns=0 -> max(1, 0) = 1 (mutante Max->Min lo dejaba en 0)."""
        _, undo = cfg_swap(**{**self.CFG, 'nntp_connections': 0})
        try:
            self.assertEqual(nz._nntp_server(),
                             'nntps://u:p@news.ej.com:563/1')
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


class TestSabnzbd(unittest.TestCase):
    """SABnzbd — downloader externo: /sab/<b64> manda el NZB por addurl."""
    CFG = dict(newznab_url='http://nz.lan/api', newznab_key='k',
               sabnzbd_url='http://sab.lan:8080', sabnzbd_key='sk',
               debrid_provider='', debrid_key='', debrid_providers={},
               nntp_host='', nntp_user='', nntp_pass='')

    def test_cfg_sin_config(self):
        _, undo = cfg_swap(sabnzbd_url='', sabnzbd_key='')
        try:
            self.assertIsNone(nz._sab_cfg())
        finally:
            undo()

    def test_cfg_ok(self):
        _, undo = cfg_swap(**self.CFG)
        try:
            self.assertEqual(nz._sab_cfg(), ('http://sab.lan:8080', 'sk'))
        finally:
            undo()

    def test_add_llama_addurl(self):
        s, calls = fake_session({'json': {'status': True, 'nzo_ids': ['SABnzbd_nzo_1']}})
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                got = nz._sab_add('https://nz.example/get/x.nzb')
        finally:
            undo()
        self.assertEqual(got, 'http://sab.lan:8080')
        p = calls[0][2]['params']
        self.assertEqual(p['mode'], 'addurl')
        self.assertEqual(p['name'], 'https://nz.example/get/x.nzb')
        self.assertEqual(p['apikey'], 'sk')
        self.assertEqual(calls[0][2].get('timeout'), 10)   # 10+1@77

    def test_add_rechazado_o_caído_none(self):
        s, _ = fake_session({'json': {'status': False}})
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                self.assertIsNone(nz._sab_add('https://x/y.nzb'))
        finally:
            undo()
        s2, _ = fake_session(Exception('down'))
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s2):
                self.assertIsNone(nz._sab_add('https://x/y.nzb'))
        finally:
            undo()
        _, undo = cfg_swap(**self.CFG)        # url no-http ni siquiera llama
        try:
            self.assertIsNone(nz._sab_add('javascript:x'))
        finally:
            undo()

    def test_sab_habilita_usenet_sin_torbox_ni_nntp(self):
        """SABnzbd solo (sin debrid ni NNTP) ya habilita la fuente usenet."""
        s, _ = fake_session({'content': _XML})
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['name'], 'SABnzbd')
        self.assertIn('/sab/', out[0]['url'])
        link = base64.urlsafe_b64decode(
            out[0]['url'].rsplit('/', 1)[-1] + '==').decode()
        self.assertEqual(link, 'https://nz.example/get/x.nzb')

    def test_cfg_url_vacia_con_key_none(self):
        """url='' con key sí -> None: el guarda exige AMBAS (mutante And@64)."""
        _, undo = cfg_swap(**{**self.CFG, 'sabnzbd_url': ''})
        try:
            self.assertIsNone(nz._sab_cfg())
        finally:
            undo()

    def test_add_no_http_no_llama(self):
        """nzb_url no-http -> None sin llamar a _session (mutante Or@70:
        el except de red lo enmascaraba — verificar que NO se pide)."""
        s, _ = fake_session({'json': {'status': True}})
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s) as m:
                self.assertIsNone(nz._sab_add('javascript:x'))
            m.assert_not_called()
        finally:
            undo()

    def test_add_caido_health_fail(self):
        """Excepción de red -> _health(False) registrado (mutante False->True)."""
        import conflux.core.state as st
        s, _ = fake_session(Exception('down'))
        _, undo = cfg_swap(**self.CFG)
        try:
            with patch.object(nz, '_session', return_value=s):
                nz._sab_add('https://x/y.nzb')
        finally:
            undo()
        self.assertGreaterEqual(st.HEALTH['SABnzbd']['fail'], 1)

    def test_las_tres_vias_a_la_vez(self):
        """TorBox + NNTP + SABnzbd: tres variantes del mismo item."""
        s, _ = fake_session({'content': _XML})
        _, undo = cfg_swap(**{**self.CFG, 'debrid_provider': 'torbox',
                              'debrid_key': 'dk',
                              'nntp_host': 'news.ej.com', 'nntp_user': 'u',
                              'nntp_pass': 'p'})
        try:
            with patch.object(nz, '_session', return_value=s):
                out = nz._newznab('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual({x['name'] for x in out},
                         {'Usenet', 'Usenet SN', 'SABnzbd'})


if __name__ == '__main__':
    unittest.main()
