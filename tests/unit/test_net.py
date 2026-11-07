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
    def setUp(self):
        net._fails.clear()               # disyuntor por host: estado fresco

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
        with patch.object(net, '_session') as m, \
                patch.object(net.time, 'sleep'):
            m.return_value.get.return_value = self._resp(b'not json')
            self.assertIsNone(net.fetch('http://x.com/'))

    def test_fetch_gigante_descarta(self):
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = self._resp(b'x' * 6_000_001)
            self.assertIsNone(net.fetch('http://x.com/'))

    def test_fetch_error_reintenta_y_none(self):
        with patch.object(net, '_session') as m, \
                patch.object(net.time, 'sleep'):
            m.return_value.get.side_effect = Exception('down')
            self.assertIsNone(net.fetch('http://x.com/'))
            self.assertEqual(m.return_value.get.call_count, 2)

    def test_get_stream_y_read_cap(self):
        """get lleva stream=True y raw.read se limita a 6_000_001 bytes."""
        r = self._resp(b'{"a":1}')
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = r
            net.fetch('http://x.com/')
        self.assertTrue(m.return_value.get.call_args[1].get('stream'))  # True@68
        r.raw.read.assert_called_once_with(6_000_001, decode_content=True)

    def test_timeout_default_7(self):
        """fetch(url) sin timeout -> get(timeout=7) (mutante 7+1@60)."""
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = self._resp(b'{"a":1}')
            net.fetch('http://x.com/')
        self.assertEqual(m.return_value.get.call_args[1].get('timeout'), 7)

    def test_backoff_sleep_03(self):
        """Entre reintentos sleep(0.3) exacto (mutante 0.3+1@80)."""
        with patch.object(net, '_session') as m, \
                patch.object(net.time, 'sleep') as sl:
            m.return_value.get.side_effect = Exception('down')
            net.fetch('http://x.com/')
        sl.assert_called_once_with(0.3)

    def test_body_6mb_exacto_parsea(self):
        """len == 6_000_000 -> SÍ parsea: la cota es >6M no >= (Gt@71)."""
        body = b'{"a":"' + b'x' * (6_000_000 - 8) + b'"}'
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = self._resp(body)
            self.assertEqual(net.fetch('http://x.com/'),
                             {'a': 'x' * (6_000_000 - 8)})

    def test_body_6mb_mas_1_descarta(self):
        """len == 6_000_001 -> descarta aunque sea JSON válido (6000000+1@71)."""
        body = b'{"a":"' + b'x' * (6_000_001 - 8) + b'"}'
        with patch.object(net, '_session') as m:
            m.return_value.get.return_value = self._resp(body)
            self.assertIsNone(net.fetch('http://x.com/'))


if __name__ == '__main__':
    unittest.main()


class TestDisyuntorHost(unittest.TestCase):
    def setUp(self):
        self._f = dict(net._fails)
        net._fails.clear()               # estado fresco: nada heredado

    def tearDown(self):
        net._fails.clear(); net._fails.update(self._f)

    def test_fallos_acumulan_strikes(self):
        import requests
        s, _ = fake_session(requests.ConnectionError('x'))
        with patch.object(net, '_session', return_value=s), \
                patch.object(net.time, 'sleep'):      # retry-sleep real fuera
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
        with patch.object(net, '_session', return_value=s), \
                patch.object(net.time, 'sleep'):
            net.fetch('http://dead.host/x', 1)
        self.assertTrue(calls, 'tras HOST_CD debe reintentar')

    def test_disyuntor_rearma_con_reloj_real(self):
        """freezegun: el breaker se rearma tras HOST_CD usando el timestamp
        que _strike escribió de verdad — no uno inyectado en _fails."""
        import requests
        from freezegun import freeze_time
        s, calls = fake_session(requests.ConnectionError('x'))
        with freeze_time() as ft, \
                patch.object(net, '_session', return_value=s), \
                patch.object(net.time, 'sleep'):
            for _ in range(2):                    # 2 fetches x 2 intentos = 4 strikes
                self.assertIsNone(net.fetch('http://dead.host/x', 1))
            self.assertEqual(net._fails['dead.host'][0], 4)
            n0 = len(calls)
            self.assertIsNone(net.fetch('http://dead.host/x', 1))
            self.assertEqual(len(calls), n0, 'disyuntor abierto: fail-fast')
            ft.tick(net.HOST_CD + 1)              # cooldown agotado
            net.fetch('http://dead.host/x', 1)
            self.assertGreater(len(calls), n0, 'tras HOST_CD reintenta')

    def test_exito_resetea_strikes(self):
        net._fails['h.host'] = (3, time.time())
        s, _ = fake_session({'json': {'ok': 1}})
        with patch.object(net, '_session', return_value=s):
            net.fetch('http://h.host/x', 1)
        self.assertNotIn('h.host', net._fails)

    def test_disyuntor_borde_exacto_host_cd(self):
        """edad == HOST_CD -> rearma: la guarda es < no <= (Lt@64, freezegun)."""
        import requests
        from freezegun import freeze_time
        with freeze_time('2025-01-01 12:00:00'):
            net._fails['dead.host'] = (4, time.time() - net.HOST_CD)
            s, calls = fake_session(requests.ConnectionError('x'))
            with patch.object(net, '_session', return_value=s), \
                    patch.object(net.time, 'sleep'):
                net.fetch('http://dead.host/x', 1)
            self.assertTrue(calls, 'edad == HOST_CD debe reintentar')

    def test_strike_purga_solo_caducados(self):
        """>HOST_CD se purga; edad == HOST_CD NO (Gt@34, freezegun)."""
        from freezegun import freeze_time
        with freeze_time('2025-01-01 12:00:00'):
            now = time.time()
            net._fails.clear()
            for i in range(199):                    # +borde+strike = 201: poda si >200
                net._fails[f'h{i}'] = (1, now - net.HOST_CD - 1)
            net._fails['borde'] = (1, now - net.HOST_CD)   # justo en el límite
            net._strike('nuevo')
        self.assertIn('borde', net._fails)
        self.assertNotIn('h0', net._fails)

    def test_strike_no_poda_bajo_umbral(self):
        """len queda en 201 -> poda; bajo el umbral la entrada sobrevive (Gt@32)."""
        from freezegun import freeze_time
        with freeze_time('2025-01-01 12:00:00'):
            net._fails.clear()
            for i in range(199):
                net._fails[f'h{i}'] = (1, time.time() - net.HOST_CD - 1)
            net._strike('nuevo')                     # len = 200, no >200
            self.assertIn('h0', net._fails, 'poda no debe dispararse a len 200')

    def test_no_sleep_tras_ultimo_intento(self):
        """El backoff va ENTRE intentos, no después del último (not-elim@79)."""
        parent = MagicMock()
        with patch.object(net, '_session') as m, \
                patch.object(net.time, 'sleep') as sl:
            m.return_value.get.side_effect = Exception('down')
            parent.attach_mock(m.return_value.get, 'get')
            parent.attach_mock(sl, 'sleep')
            net.fetch('http://x.com/')
        seq = [c[0] for c in parent.mock_calls]
        self.assertEqual(seq, ['get', 'sleep', 'get'])

    def test_session_cap_64(self):
        """len(_SESS)>=64 -> se vacía el pool antes de crear otra (64+1/GtE@19)."""
        for i in range(64):
            net._SESS[f'old{i}'] = MagicMock()
        s = net._session('new.host')
        self.assertIs(net._SESS['new.host'], s)
        self.assertNotIn('old0', net._SESS, 'a 64 sesiones el pool se reinicia')
        self.assertEqual(len(net._SESS), 1)


class TestPublicUrl(unittest.TestCase):
    """_public_url: guard SSRF de URLs configurables (epg_url, M3U_URL)."""

    def setUp(self):
        # tests/__init__ stubéa _public_url sin DNS; aquí probamos el real
        self._p = patch.object(net, '_public_url', net._real_public_url)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    @staticmethod
    def _gai(ips):
        return [(2, 1, 6, '', (ip, 80)) for ip in ips]

    def _pu(self, url, ips=('93.184.216.34',)):
        with patch.object(net.socket, 'getaddrinfo', return_value=self._gai(ips)):
            return net._public_url(url)

    def test_publica_ok(self):
        self.assertTrue(self._pu('https://epg.example/e.xml.gz'))

    def test_metadata_cloud_bloqueada(self):
        self.assertFalse(self._pu('http://x.internal/', ('169.254.169.254',)))

    def test_ipv6_linklocal_bloqueada(self):
        self.assertFalse(self._pu('http://x/', ('fe80::1',)))

    def test_una_ip_mala_basta(self):
        self.assertFalse(self._pu('http://x/', ('93.184.216.34', '169.254.169.254')))

    def test_multicast_y_reserved_bloqueados(self):
        self.assertFalse(self._pu('http://x/', ('239.1.1.1',)))
        self.assertFalse(self._pu('http://x/', ('240.0.0.1',)))

    def test_lan_permitida(self):
        """RFC1918/loopback sí: Prowlarr, NAS y xmltv local son uso legitimo."""
        self.assertTrue(self._pu('http://nas.lan/e.xml', ('192.168.1.10',)))
        self.assertTrue(self._pu('http://prowlarr/api', ('127.0.0.1',)))

    def test_esquema_no_http(self):
        self.assertFalse(self._pu('file:///etc/passwd'))
        self.assertFalse(self._pu('ftp://x/f'))

    def test_dns_fallo(self):
        with patch.object(net.socket, 'getaddrinfo', side_effect=OSError):
            self.assertFalse(net._public_url('http://noexiste.invalid/'))

    def test_sin_host(self):
        self.assertFalse(self._pu('http:///path'))

    def test_puerto_https_443_http_80(self):
        """getaddrinfo recibe el puerto por defecto del esquema (Eq/443/80@47)."""
        for url, port in (('https://x/e', 443), ('http://x/e', 80),
                          ('http://x:8080/e', 8080)):
            with patch.object(net.socket, 'getaddrinfo',
                              return_value=self._gai(('93.184.216.34',))) as g:
                net._public_url(url)
            self.assertEqual(g.call_args[0][1], port, url)

    def test_dns_sin_respuestas_false(self):
        """getaddrinfo -> [] (host existe pero sin registros) -> False (False@50)."""
        with patch.object(net.socket, 'getaddrinfo', return_value=[]):
            self.assertFalse(net._public_url('http://x/'))


class TestEpgSsrf(unittest.TestCase):
    """epg: la URL configurable pasa por _public_url y los redirects se revalidan."""

    def test_epg_url_no_publica_no_descarga(self):
        import conflux.infra.epg as E
        with patch.dict(E.CFG, {'epg_url': 'http://169.254.169.254/'}), \
                patch.object(E, '_public_url', return_value=False), \
                patch.object(E, '_OPENER') as op:
            self.assertIsNone(E._build([]))
            op.open.assert_not_called()

    def test_redirect_a_interno_rechazado(self):
        import conflux.infra.epg as E
        h = E._ChkRedirect()
        with patch.object(E, '_public_url', return_value=False):
            self.assertIsNone(h.redirect_request(
                MagicMock(), None, 302, '', {}, 'http://169.254.169.254/latest'))
