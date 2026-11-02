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
