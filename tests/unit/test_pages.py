# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.web.pages — HTML admin/configure."""
import unittest
from unittest.mock import patch

import conflux.web.pages as pg
from tests import cfg_swap


class TestPages(unittest.TestCase):
    def test_admin_html(self):
        h = pg._admin_page()
        self.assertIn('<', h)
        self.assertIn('html', h.lower())

    def test_configure_html(self):
        h = pg._configure_page()
        self.assertIn('<form', h)

    def test_trakt_auth_html(self):
        h = pg._trakt_auth_page()
        self.assertIn('<', h)

    def test_health_page_json(self):
        d = pg._health_page()
        self.assertIn('sources', d)

    def test_trakt_status_json(self):
        d = pg._trakt_status()
        self.assertIsInstance(d, dict)


if __name__ == '__main__':
    unittest.main()





class TestTraktAuthBranches(unittest.TestCase):
    def setUp(self):
        self._dev = dict(pg._TRAKT_DEV)

    def tearDown(self):
        pg._TRAKT_DEV.clear(); pg._TRAKT_DEV.update(self._dev)

    def test_sin_credenciales_avisa(self):
        _, undo = cfg_swap(trakt_client_id='', trakt_client_secret='')
        try:
            html = pg._trakt_auth_page()
        finally:
            undo()
        self.assertIn('trakt.tv/oauth/applications', html)

    def test_conectado_muestra_exp(self):
        _, undo = cfg_swap(trakt_client_id='C', trakt_client_secret='S',
                           trakt_access_token='AT', trakt_expires=0)
        try:
            html = pg._trakt_auth_page()
        finally:
            undo()
        self.assertIn('Trakt conectado', html)

    def test_device_flow_muestra_codigo(self):
        _, undo = cfg_swap(trakt_client_id='C', trakt_client_secret='S',
                           trakt_access_token='')
        try:
            with patch.object(pg, '_trakt_device_start', return_value={
                    'verification_url': 'https://trakt.tv/activate',
                    'user_code': 'ABCD'}):
                html = pg._trakt_auth_page()
        finally:
            undo()
        self.assertIn('ABCD', html)

    def test_device_flow_fallo(self):
        _, undo = cfg_swap(trakt_client_id='C', trakt_client_secret='S',
                           trakt_access_token='')
        try:
            with patch.object(pg, '_trakt_device_start', return_value=None):
                html = pg._trakt_auth_page()
        finally:
            undo()
        self.assertIn('No se pudo iniciar', html)


class TestLandingPage(unittest.TestCase):
    def test_landing_host_loopback(self):
        h = pg._landing_page('127.0.0.1:9700', tls=False)
        self.assertIn('stremio://127.0.0.1:9700/manifest.json', h)
        self.assertIn('http://127.0.0.1:9700/manifest.json', h)

    def test_landing_host_lan(self):
        """La URL de instalación sigue al Host header: LAN/dominio también."""
        h = pg._landing_page('192.168.1.50:9700', tls=False)
        self.assertIn('stremio://192.168.1.50:9700/manifest.json', h)

    def test_landing_tls_https(self):
        h = pg._landing_page('addon.example.com', tls=True)
        self.assertIn('https://addon.example.com/manifest.json', h)
        self.assertNotIn('http://addon', h)


class TestCfgFormFields(unittest.TestCase):
    def test_checkboxes_ultimo_gana(self):
        """hidden 0 + checkbox 1 -> el último valor gana (patrón HTML form)."""
        cfg = {}
        pg._cfg_form_fields({'stream_prefetch': ['0', '1'],
                             'torrent_health': ['0']}, cfg)
        self.assertTrue(cfg['stream_prefetch'])
        self.assertFalse(cfg['torrent_health'])

    def test_stream_max_gb_int_y_basura(self):
        cfg = {}
        pg._cfg_form_fields({'stream_max_gb': ['4']}, cfg)
        self.assertEqual(cfg['stream_max_gb'], 4)
        pg._cfg_form_fields({'stream_max_gb': ['abc']}, cfg)
        self.assertEqual(cfg['stream_max_gb'], 4)      # basura no pisa el valor

    def test_csv_listas(self):
        cfg = {}
        pg._cfg_form_fields({'stream_sort': [' calidad , idioma ,, ']}, cfg)
        self.assertEqual(cfg['stream_sort'], ['calidad', 'idioma'])

    def test_secretos_solo_si_vienen(self):
        """Campo secreto vacío NO pisa el valor previo; con valor sí."""
        cfg = {'torznab_key': 'VIEJA'}
        pg._cfg_form_fields({'torznab_key': ['  ']}, cfg)
        self.assertEqual(cfg['torznab_key'], 'VIEJA')
        pg._cfg_form_fields({'torznab_key': ['NUEVA']}, cfg)
        self.assertEqual(cfg['torznab_key'], 'NUEVA')

    def test_stream_langs_vacio_conserva(self):
        cfg = {'stream_langs': ['es']}
        with patch.dict(pg.CFG, {'stream_langs': ['es', 'en']}):
            pg._cfg_form_fields({'stream_langs': ['']}, cfg)
        self.assertEqual(cfg['stream_langs'], ['es', 'en'])  # fallback CFG


class TestTraktHelpers(unittest.TestCase):
    def test_trakt_status_estados(self):
        with patch.dict(pg.CFG, {'trakt_access_token': 'tok'}), \
                patch.object(pg, '_TRAKT_DEV', None):
            self.assertEqual(pg._trakt_status(), {'state': 'ok'})
        with patch.dict(pg.CFG, {'trakt_access_token': ''}), \
                patch.object(pg, '_TRAKT_DEV', None):
            self.assertEqual(pg._trakt_status(), {'state': 'none'})
        with patch.dict(pg.CFG, {'trakt_access_token': ''}), \
                patch.object(pg, '_TRAKT_DEV', {'code': 'x'}):
            self.assertEqual(pg._trakt_status(), {'state': 'pending'})

    def test_cfg_trakt_link_solo_si_falta_token(self):
        with patch.dict(pg.CFG, {'trakt_client_id': 'cid',
                                 'trakt_access_token': ''}):
            self.assertIn('/auth/trakt', pg._cfg_trakt_link())
        with patch.dict(pg.CFG, {'trakt_client_id': 'cid',
                                 'trakt_access_token': 'tok'}):
            self.assertEqual(pg._cfg_trakt_link(), '')


class TestCsvList(unittest.TestCase):
    def test_limpia_y_descarta_vacios(self):
        self.assertEqual(pg._csv_list({'k': [' a ,,b , ']}, 'k'), ['a', 'b'])
        self.assertEqual(pg._csv_list({}, 'k'), [])
