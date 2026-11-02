# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position
"""Unit tests conflux.infra.net — sesiones + fetch."""
import unittest
from unittest.mock import MagicMock, patch

from conflux.infra import net


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
