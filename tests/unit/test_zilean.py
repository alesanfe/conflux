# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.zilean — DMM hashlists -> streams cached."""
import unittest
from unittest.mock import patch

import conflux.infra.zilean as z
from tests import cfg_swap, fake_session


class TestNum(unittest.TestCase):
    def test_int_str_float(self):
        self.assertEqual(z._num('42'), 42)
        self.assertEqual(z._num(3.9), 3)

    def test_basura_none(self):
        self.assertIsNone(z._num('abc'))
        self.assertIsNone(z._num(None))


class TestZilean(unittest.TestCase):
    def test_ssrf_linklocal_no_fetch(self):
        """zilean_url a metadata-cloud (169.254.x) no se fetchea — el guard
        _public_url la bloquea como epg_url ya hacía."""
        _, undo = cfg_swap(zilean_url='http://169.254.169.254')
        try:
            with patch.object(z, '_session') as ms:
                self.assertEqual(z._zilean('movie', 'tt0137523'), [])
        finally:
            undo()
        ms.assert_not_called()

    def test_sin_url(self):
        _, undo = cfg_swap(zilean_url='')
        try:
            self.assertEqual(z._zilean('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_no_tt(self):
        _, undo = cfg_swap(zilean_url='http://z.lan')
        try:
            self.assertEqual(z._zilean('movie', 'kitsu:1'), [])
        finally:
            undo()

    def test_season_episode_params(self):
        s, calls = fake_session({'json': []})
        _, undo = cfg_swap(zilean_url='http://z.lan/')
        try:
            with patch.object(z, '_session', return_value=s):
                z._zilean('series', 'tt0944947:1:2')
        finally:
            undo()
        params = calls[0][2]['params']
        self.assertEqual(params['ImdbId'], 'tt0944947')
        self.assertEqual(params['Season'], '1')
        self.assertEqual(params['Episode'], '2')
        self.assertEqual(calls[0][2]['timeout'], 10)   # mutante 10+1@59

    def test_items_filtrados(self):
        items = [{'infoHash': 'H' * 40, 'filename': 'x.mkv', 'filesize': '100'},
                 {'filename': 'sin hash'},                     # fuera
                 'no-dict',                                     # fuera
                 {'info_hash': 'A' * 40, 'title': 'y.mkv'}]     # claves alternas
        s, _ = fake_session({'json': items})
        _, undo = cfg_swap(zilean_url='http://z.lan')
        try:
            with patch.object(z, '_session', return_value=s):
                out = z._zilean('movie', 'tt0137523')
        finally:
            undo()
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]['infoHash'], 'h' * 40)   # minúscula
        self.assertTrue(out[0]['behaviorHints']['cached'])
        self.assertEqual(out[0]['behaviorHints']['videoSize'], 100)

    def test_dict_con_results(self):
        s, _ = fake_session({'json': {'results': [
            {'infoHash': 'b' * 40, 'title': 'x'}]}})
        _, undo = cfg_swap(zilean_url='http://z.lan')
        try:
            with patch.object(z, '_session', return_value=s):
                self.assertEqual(len(z._zilean('movie', 'tt0137523')), 1)
        finally:
            undo()

    def test_error_red_vacio(self):
        s, _ = fake_session(Exception('down'))
        _, undo = cfg_swap(zilean_url='http://z.lan')
        try:
            with patch.object(z, '_session', return_value=s):
                self.assertEqual(z._zilean('movie', 'tt0137523'), [])
        finally:
            undo()

    def test_url_vacia_ni_intenta(self):
        """zilean_url='' -> [] sin llamar a _session (mutante Or@51: el
        except de red lo enmascaraba — hay que verificar que NO se pide)."""
        s, _ = fake_session({'json': [{'infoHash': 'a' * 40, 'filename': 'x'}]})
        _, undo = cfg_swap(zilean_url='')
        try:
            with patch.object(z, '_session', return_value=s) as m:
                self.assertEqual(z._zilean('movie', 'tt0137523'), [])
            m.assert_not_called()
        finally:
            undo()

    def test_session_con_hostname(self):
        """_session se clavea por el host real de la url (mutante Or@58:
        `hostname or 'zilean'` -> and claveaba siempre 'zilean')."""
        s, _ = fake_session({'json': []})
        _, undo = cfg_swap(zilean_url='http://z.lan:8181')
        try:
            with patch.object(z, '_session', return_value=s) as m:
                z._zilean('movie', 'tt0137523')
            m.assert_called_once_with('z.lan')
        finally:
            undo()

    def test_cap_40(self):
        items = [{'infoHash': '%040x' % i, 'filename': 'f%d' % i}
                 for i in range(41)]                  # 41 -> cap
        s, _ = fake_session({'json': items})
        _, undo = cfg_swap(zilean_url='http://z.lan')
        try:
            with patch.object(z, '_session', return_value=s):
                self.assertEqual(len(z._zilean('movie', 'tt0137523')), 40)
        finally:
            undo()


if __name__ == '__main__':
    unittest.main()
