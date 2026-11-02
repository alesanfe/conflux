# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.net — sesiones + fetch."""
import time
import unittest
from unittest.mock import MagicMock, patch

from conflux.infra import net
from tests import fake_session


class TestSession(unittest.TestCase):
    def test_session_por_host(self):
        s1 = net._session('a.com')
        s2 = net._session('a.com')
        s3 = net._session('b.com')
        self.assertIs(s1, s2)
        self.assertIsNot(s1, s3)


class TestFetch(unittest.TestCase):
    def _resp(self, body=b'{"a":1}', ok=True):
        r = MagicMock()
        r.raw.read.return_value = body
        if not ok:
            r.raw.read.side_effect = Exception('net')
        return r

    def test_fetch_ok(self):
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = self._resp(b'{"a":1}')
            self.assertEqual(net.fetch('http://x.com/'), {'a': 1})

    def test_fetch_json_mal(self):
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = self._resp(b'not json')
            self.assertIsNone(net.fetch('http://x.com/'))

    def test_fetch_gigante_descarta(self):
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = self._resp(b'x' * 6_000_001)
            self.assertIsNone(net.fetch('http://x.com/'))

    def test_fetch_error_reintenta_y_none(self):
        with patch.object(net, '_session') as m:
            m.return_value.get.side_effect = Exception('down')
            self.assertIsNone(net.fetch('http://x.com/'))
            self.assertEqual(m.return_value.get.call_count, 2)


if __name__ == '__main__':
    unittest.main()


class TestDisyuntorHost(unittest.TestCase):
    def setUp(self):
        self._f = dict(net._fails)

    def tearDown(self):
        net._fails.clear(); net._fails.update(self._f)

    def test_fallos_acumulan_strikes(self):
        import requests
        s, _ = fake_session(requests.ConnectionError('x'))
        with patch.object(net, '_session', return_value=s):
            for _ in range(2):
                self.assertIsNone(net.fetch('http://dead.host/x', 1))
        # 2 llamadas x 2 intentos = 4 strikes
        self.assertEqual(net._fails['dead.host'][0], 4)

    def test_disyuntor_abierto_fail_fast(self):
        import requests
        net._fails['dead.host'] = (4, time.time())
        s, calls = fake_session(requests.ConnectionError('x'))
        with patch.object(net, '_session', return_value=s):
            self.assertIsNone(net.fetch('http://dead.host/x', 1))
        self.assertEqual(calls, [], 'disyuntor abierto no debe tocar la red')

    def test_disyuntor_se_rearma_tras_cd(self):
        import requests
        net._fails['dead.host'] = (4, time.time() - 61)   # cooldown expirado
        s, calls = fake_session(requests.ConnectionError('x'))
        with patch.object(net, '_session', return_value=s):
            net.fetch('http://dead.host/x', 1)
        self.assertTrue(calls, 'tras HOST_CD debe reintentar')

    def test_exito_resetea_strikes(self):
        net._fails['h.host'] = (3, time.time())
        s, _ = fake_session({'json': {'ok': 1}})
        with patch.object(net, '_session', return_value=s):
            net.fetch('http://h.host/x', 1)
        self.assertNotIn('h.host', net._fails)
