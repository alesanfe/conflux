# pylint: disable=missing-docstring,protected-access,import-outside-toplevel
"""Unit tests conflux.infra.torrent_health — veredictos de swarm por infoHash."""
import time
import unittest

import conflux.infra.torrent_health as TH

H_LIVE = 'a' * 40
H_DEAD = 'b' * 40
H_PRIV = 'c' * 40


class TestHashHealth(unittest.TestCase):
    """_hash_health: 0-seed + sin hints fuera; el resto se mantiene."""

    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()
        self._todo = []
        self._sub = TH._POOL_BG.submit
        TH._POOL_BG.submit = lambda f, *a: self._todo.append((f, a))

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)
        TH._POOL_BG.submit = self._sub

    def _st(self, h, sources=None):
        s = {'infoHash': h}
        if sources:
            s['sources'] = sources
        return s

    def test_muerto_confirmado_sin_hints_fuera(self):
        TH._SEED[H_DEAD] = (-1, time.time())        # 0 trackers + 0 DHT
        out = [self._st(H_DEAD)]
        TH._hash_health(out)
        self.assertEqual(out, [])

    def test_muerto_confirmado_con_hints_queda(self):
        TH._SEED[H_PRIV] = (-1, time.time())
        s = self._st(H_PRIV, sources=['dht:x'])
        out = [s]
        TH._hash_health(out)
        self.assertEqual(out, [s])

    def test_seeds_positivos_queda(self):
        TH._SEED[H_LIVE] = (7, time.time())
        s = self._st(H_LIVE)
        out = [s]
        TH._hash_health(out)
        self.assertEqual(out, [s])

    def test_desconocido_queda_y_encola(self):
        s = self._st(H_DEAD)
        out = [s]
        TH._hash_health(out)
        self.assertEqual(out, [s])
        self.assertEqual(len(self._todo), 1)          # scrape encolado

    def test_veredicto_caducado_reintenta(self):
        TH._SEED[H_DEAD] = (-1, time.time() - TH._TTL - 10)
        s = self._st(H_DEAD)
        out = [s]
        TH._hash_health(out)
        self.assertEqual(out, [s])                    # caducado: no es dead
        self.assertEqual(len(self._todo), 1)          # y se re-scrapea

    def test_muerto_con_hints_al_fondo(self):
        TH._SEED[H_PRIV] = (-1, time.time())
        vivo = self._st(H_LIVE)
        muerto = self._st(H_PRIV, sources=['tracker:udp://x'])
        out = [muerto, vivo]
        TH._hash_health(out)
        self.assertEqual(out, [vivo, muerto])       # se mantiene pero hundido

    def test_persist_roundtrip(self):
        import tempfile, os
        TH._SEED[H_LIVE] = (9, time.time())
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 's.json')
            TH._LAST_SAVE[0] = 0
            TH._seed_save(p)
            TH._SEED.clear()
            TH._seed_load(p)
        self.assertEqual(TH._SEED[H_LIVE][0], 9)

    def test_mixto(self):
        TH._SEED[H_DEAD] = (-1, time.time())
        TH._SEED[H_LIVE] = (3, time.time())
        out = [self._st(H_LIVE), self._st(H_DEAD),
               self._st(H_PRIV, sources=['tracker:udp://x']),
               {'url': 'http://x/y.mp4'}]
        TH._hash_health(out)
        self.assertEqual(len(out), 3)
        self.assertNotIn(H_DEAD, [str(s.get('infoHash', '')).lower() for s in out])


class TestDhtVerdict(unittest.TestCase):
    """_scrape_submit: 0-seeds de tracker -> DHT decide el veredicto final."""

    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def test_cero_tracker_cero_dht_muerto(self):
        import unittest.mock as um
        with um.patch.object(TH, '_scrape_hashes', return_value={H_DEAD: 0}), \
                um.patch.object(TH, '_dht_peers', return_value=0):
            TH._scrape_submit([(H_DEAD, [])])
        self.assertEqual(TH._SEED[H_DEAD][0], -1)

    def test_cero_tracker_dht_vivo(self):
        import unittest.mock as um
        with um.patch.object(TH, '_scrape_hashes', return_value={H_DEAD: 0}), \
                um.patch.object(TH, '_dht_peers', return_value=7):
            TH._scrape_submit([(H_DEAD, [])])
        self.assertEqual(TH._SEED[H_DEAD][0], 99)   # vivo via DHT

    def test_seeds_directo_sin_dht(self):
        import unittest.mock as um
        with um.patch.object(TH, '_scrape_hashes', return_value={H_LIVE: 5}), \
                um.patch.object(TH, '_dht_peers') as dp:
            TH._scrape_submit([(H_LIVE, [])])
        self.assertEqual(TH._SEED[H_LIVE][0], 5)
        dp.assert_not_called()                       # seeds>0: DHT no hace falta


class TestAnnounceVerdict(unittest.TestCase):
    """_scrape_submit: 0-seed con hints -> announce a sus propios trackers."""

    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def _run(self, st):
        import unittest.mock as um
        urls = ['udp://tracker.test:1337/announce']
        with um.patch.object(TH, '_scrape_hashes', return_value={H_DEAD: 0}), \
                um.patch.object(TH, '_announce_hints', return_value=st) as ah, \
                um.patch.object(TH, '_dht_peers', return_value=0) as dp:
            TH._scrape_submit([(H_DEAD, urls)])
        return ah, dp

    def test_announce_vivo(self):
        ah, dp = self._run('alive')
        self.assertEqual(TH._SEED[H_DEAD][0], 99)
        ah.assert_called_once()
        dp.assert_not_called()                   # announce resolvió: DHT no hace falta

    def test_announce_muerto(self):
        self._run('dead')
        self.assertEqual(TH._SEED[H_DEAD][0], -2)

    def test_announce_unknown_cae_a_dht(self):
        _, dp = self._run('unknown')
        self.assertEqual(TH._SEED[H_DEAD][0], -1)
        dp.assert_called_once_with(H_DEAD)

    def test_menos_de_2_respuestas_unknown(self):
        import unittest.mock as um
        with um.patch.object(TH, '_announce_udp', return_value=None):
            r = TH._announce_hints('a' * 40, ['udp://t1:1/x', 'udp://t2:2/x'])
        self.assertEqual(r, 'unknown')           # todos timeout = tracker caído

    def test_dos_muetos_dan_dead(self):
        import unittest.mock as um
        with um.patch.object(TH, '_announce_udp',
                             return_value={'seeds': 0, 'leechers': 0, 'peers': 0}):
            r = TH._announce_hints('a' * 40, ['udp://t1:1/x', 'udp://t2:2/x'])
        self.assertEqual(r, 'dead')              # 2 trackers responden 0 = muerto

    def test_uno_vivo_alive(self):
        import unittest.mock as um
        with um.patch.object(TH, '_announce_udp', return_value=None):
            r = TH._announce_hints('a' * 40, ['udp://t1:1/x'])
        self.assertEqual(r, 'unknown')
        with um.patch.object(TH, '_announce_udp',
                             side_effect=[None, {'seeds': 3, 'leechers': 1, 'peers': 2}]):
            r = TH._announce_hints('a' * 40, ['udp://t1:1/x', 'udp://t2:2/x'])
        self.assertEqual(r, 'alive')


class TestBencode(unittest.TestCase):
    def test_roundtrip(self):
        obj = {b'a': {b'id': b'x' * 20}, b't': b'aa', b'y': b'r', b'z': [1, b'k']}
        b = TH._benc(obj)
        out, off = TH._bdec(b)
        self.assertEqual(out, obj)
        self.assertEqual(off, len(b))


class TestScrapeParser(unittest.TestCase):
    """_scrape_tracker: parseo del protocolo UDP (mock de socket)."""

    def test_respuesta_parseada(self):
        import struct
        import unittest.mock as um
        h1, h2 = 'aa' * 20, 'bb' * 20
        class FakeSock:
            def __init__(self, *a):
                self._n = 0
                self._tx = 0
            def settimeout(self, t): pass
            def sendto(self, b, dst):
                self._tx = struct.unpack('!I', b[12:16])[0]
                self._n += 1
            def recvfrom(self, n):
                if self._n == 1:
                    return struct.pack('!IIQ', 0, self._tx, 12345), None
                return struct.pack('!II', 2, self._tx) + \
                    struct.pack('!III', 9, 0, 0) + struct.pack('!III', 0, 0, 0), None

        with um.patch.object(TH.socket, 'socket', FakeSock), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._scrape_tracker('tracker.test', 1337, [h1, h2])
        self.assertEqual(r.get(h1), 9)
        self.assertEqual(r.get(h2), 0)

    def test_tracker_caido_vacio(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'gethostbyname', side_effect=OSError):
            self.assertEqual(TH._scrape_tracker('nohost', 1, ['a' * 40]), {})


class TestAnnounceUdp(unittest.TestCase):
    """_announce_udp: handshake connect+announce -> seeds/peers | 'deny' | None."""
    import struct as _st

    def _sock(self, announce_act=1, seeds=7, leechers=3, npeers=2):
        st = self._st
        class FakeSock:
            def __init__(self, *a):
                self._n = 0
            def settimeout(self, t): pass
            def close(self): pass
            def sendto(self, b, dst):
                self._tx = st.unpack('!I', b[12:16])[0]
                self._n += 1
            def recvfrom(self, n):
                if self._n == 1:
                    return st.pack('!IIQ', 0, self._tx, 999), None   # connect
                peers = b'\x01\x02\x03\x04\x05\x06' * npeers
                return st.pack('!II', announce_act, self._tx) + \
                    st.pack('!III', 1800, leechers, seeds) + peers, None
        return FakeSock

    def test_announce_respuesta(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', self._sock()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._announce_udp('t.test', 1337, 'a' * 40)
        self.assertEqual(r, {'seeds': 7, 'leechers': 3, 'peers': 2})

    def test_announce_denegado(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', self._sock(announce_act=3)), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._announce_udp('t.test', 1337, 'a' * 40)
        self.assertEqual(r, 'deny')

    def test_announce_timeout_none(self):
        import unittest.mock as um
        class DeadSock:
            def __init__(self, *a): pass
            def settimeout(self, t): pass
            def close(self): pass
            def sendto(self, b, dst): pass
            def recvfrom(self, n): raise TimeoutError()
        with um.patch.object(TH.socket, 'socket', DeadSock), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            self.assertIsNone(TH._announce_udp('t.test', 1337, 'a' * 40))

    def test_dns_muerto_none(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'gethostbyname', side_effect=OSError):
            self.assertIsNone(TH._announce_udp('nohost', 1, 'a' * 40))


class TestAnnounceHttp(unittest.TestCase):
    """_announce_http: GET announce -> seeds/peers | 'deny' | None."""

    def _resp(self, d):
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n): return TH._benc(d)
        return R()

    def test_respuesta_compacta(self):
        import unittest.mock as um
        d = {b'complete': 5, b'incomplete': 2, b'peers': b'\x00' * 12}
        with um.patch('urllib.request.urlopen', return_value=self._resp(d)):
            r = TH._announce_http('https://t.test/announce', 'a' * 40)
        self.assertEqual(r, {'seeds': 5, 'leechers': 2, 'peers': 2})

    def test_failure_reason_deny(self):
        import unittest.mock as um
        with um.patch('urllib.request.urlopen',
                      return_value=self._resp({b'failure reason': b'no torrent'})):
            self.assertEqual(TH._announce_http('https://t/a', 'a' * 40), 'deny')

    def test_excepcion_none(self):
        import unittest.mock as um
        with um.patch('urllib.request.urlopen', side_effect=OSError):
            self.assertIsNone(TH._announce_http('https://t/a', 'a' * 40))


class TestKrpc(unittest.TestCase):
    """_krpc/_dht_nodes/_dht_peers con sockets falsos."""

    def test_krpc_respuesta_r(self):
        import unittest.mock as um
        tid_holder = []
        class FakeSock:
            def sendto(self, b, dst):
                d, _ = TH._bdec(b)
                tid_holder.append(d[b't'])
            def recvfrom(self, n):
                return TH._benc({b't': tid_holder[0], b'y': b'r',
                                 b'r': {b'id': b'x' * 20}}), None
        self.assertEqual(TH._krpc(FakeSock(), ('1.2.3.4', 1), 'ping', {}),
                         {b'id': b'x' * 20})

    def test_krpc_timeout_none(self):
        class DeadSock:
            def sendto(self, b, dst): pass
            def recvfrom(self, n): raise TimeoutError()
        self.assertIsNone(TH._krpc(DeadSock(), ('1.2.3.4', 1), 'ping', {}))

    def test_krpc_tid_ajeno_none(self):
        class FakeSock:
            def sendto(self, b, dst): pass
            def recvfrom(self, n):
                return TH._benc({b't': b'ZZ', b'y': b'r', b'r': {}}), None
        self.assertIsNone(TH._krpc(FakeSock(), ('1.2.3.4', 1), 'ping', {}))

    def test_dht_nodes_usa_cache(self):
        old = TH._DHT_NODES
        TH._DHT_NODES = [('1.2.3.4', 6881)]
        try:
            self.assertEqual(TH._dht_nodes(None), [('1.2.3.4', 6881)])
        finally:
            TH._DHT_NODES = old

    def test_dht_peers_cuenta_values(self):
        import unittest.mock as um
        peers = [b'\x01\x02\x03\x04\x05\x06', b'\x07\x08\x09\x0a\x0b\x0c']
        with um.patch.object(TH.socket, 'socket', lambda *a: um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes', return_value=[('1.2.3.4', 1)]), \
                um.patch.object(TH, '_krpc',
                                return_value={b'values': peers, b'nodes': b''}):
            self.assertEqual(TH._dht_peers('a' * 40), 2)

    def test_dht_peers_nodo_aprende_siguiente(self):
        import unittest.mock as um
        import socket as so
        import struct as st
        nb = b'n' * 20 + so.inet_aton('9.9.9.9') + st.pack('!H', 6881)
        respuestas = [{b'values': [], b'nodes': nb},
                      {b'values': [b'p' * 6], b'nodes': b''}]
        with um.patch.object(TH.socket, 'socket', lambda *a: um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes', return_value=[('1.2.3.4', 1)]), \
                um.patch.object(TH, '_krpc', side_effect=respuestas):
            self.assertEqual(TH._dht_peers('a' * 40), 1)   # hallado en el 2º nodo

    def test_dht_peers_socket_roto_0(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', side_effect=OSError):
            self.assertEqual(TH._dht_peers('a' * 40), 0)


class TestBdecodeEdges(unittest.TestCase):
    def test_truncado_explota(self):
        with self.assertRaises(Exception):
            TH._bdec(b'i12')                        # sin 'e' de cierre

    def test_benc_str_y_tipo_malo(self):
        self.assertEqual(TH._benc('ok'), b'2:ok')
        with self.assertRaises(TypeError):
            TH._benc(object())

    def test_bdec_offset_no_zero(self):
        v, off = TH._bdec(b'xxi5e', 2)
        self.assertEqual((v, off), (5, 5))


class TestSeedPersist(unittest.TestCase):
    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def test_load_corrupto_no_muere(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as f:
            f.write('{{{{no json')
            path = f.name
        try:
            TH._seed_load(path)                      # no propaga
        finally:
            os.unlink(path)
        self.assertEqual(TH._SEED, {})

    def test_persist_roundtrip_borde_ttl(self):
        """freezegun: el veredicto persistido sobrevive justo antes del TTL y
        se descarta en el borde exacto (now-ts < _TTL)."""
        import tempfile, os
        from freezegun import freeze_time
        h = 'c' * 40
        with tempfile.TemporaryDirectory() as td, freeze_time() as ft:
            p = os.path.join(td, 's.json')
            TH._SEED[h] = (7, time.time())
            TH._LAST_SAVE[0] = 0
            TH._seed_save(p)
            TH._SEED.clear()
            ft.tick(TH._TTL - 1)                  # 1s antes de caducar
            TH._seed_load(p)
            self.assertIn(h, TH._SEED)
            TH._SEED.clear()
            ft.tick(1)                            # justo en el borde: caduca
            TH._seed_load(p)
            self.assertNotIn(h, TH._SEED)

    def test_load_salta_caducados(self):
        import tempfile, os, json
        old = time.time() - TH._TTL - 10
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as f:
            json.dump({'a' * 40: [5, time.time()], 'b' * 40: [3, old]}, f)
            path = f.name
        try:
            TH._seed_load(path)
        finally:
            os.unlink(path)
        self.assertIn('a' * 40, TH._SEED)
        self.assertNotIn('b' * 40, TH._SEED)

    def test_save_throttle_60s(self):
        import tempfile, os, json
        TH._LAST_SAVE[0] = time.time()               # recién guardado -> no escribe
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 's.json')
            TH._SEED['a' * 40] = (5, time.time())
            TH._seed_save(p)
            self.assertFalse(os.path.exists(p))
            TH._LAST_SAVE[0] = 0                     # fuerza escritura
            TH._seed_save(p)
            self.assertTrue(os.path.exists(p))
            with open(p) as f:
                self.assertEqual(json.load(f)['a' * 40][0], 5)
