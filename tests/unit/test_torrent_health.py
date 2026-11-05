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
        import tempfile
        import os
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


class TestDhtNodesCold(unittest.TestCase):
    """_dht_nodes sin caché: find_node a bootstrap -> lista compacta parseada."""

    def setUp(self):
        self._old = TH._DHT_NODES
        TH._DHT_NODES = []

    def tearDown(self):
        TH._DHT_NODES = self._old

    @staticmethod
    def _sock_krpc(r_dict):
        class S:
            _tid = None
            def sendto(self, b, dst):
                d, _ = TH._bdec(b)
                self._tid = d[b't']
            def recvfrom(self, n):
                return TH._benc({b't': self._tid, b'y': b'r',
                                 b'r': r_dict}), None
        return S()

    def test_find_node_parsea_nodos_compactos(self):
        import unittest.mock as um
        import socket as so
        import struct as st
        nb = b'n' * 20 + so.inet_aton('8.8.8.8') + st.pack('!H', 6881)
        with um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            out = TH._dht_nodes(self._sock_krpc({b'nodes': nb}))
        self.assertIn(('8.8.8.8', 6881), out)
        self.assertEqual(TH._DHT_NODES, out)          # queda cacheada

    def test_bootstrap_muerto_sigue_al_siguiente(self):
        import unittest.mock as um
        import socket as so
        import struct as st
        nb = b'n' * 20 + so.inet_aton('8.8.4.4') + st.pack('!H', 6881)
        socks = iter([None, self._sock_krpc({b'nodes': nb}), None])
        with um.patch.object(TH.socket, 'gethostbyname',
                             side_effect=[OSError, '1.2.3.4', '1.2.3.4']):
            out = TH._dht_nodes(next(s for s in socks if s))
        self.assertIn(('8.8.4.4', 6881), out)

    def test_dns_bootstrap_muerto_vacio(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'gethostbyname', side_effect=OSError):
            self.assertEqual(TH._dht_nodes(um.MagicMock()), [])


class TestDhtPeersEdges(unittest.TestCase):
    """_dht_peers: ramas de seen/None/close que el happy path no toca."""

    def test_nodo_duplicado_salta_seen(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', return_value=um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes',
                                return_value=[('1.1.1.1', 1), ('1.1.1.1', 1)]), \
                um.patch.object(TH, '_krpc',
                                return_value={b'values': [], b'nodes': b''}) as k:
            self.assertEqual(TH._dht_peers('a' * 40), 0)
        self.assertEqual(k.call_count, 1)               # el dup ni se consulta

    def test_krpc_none_pasa_al_siguiente_nodo(self):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', return_value=um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes',
                                return_value=[('1.1.1.1', 1), ('2.2.2.2', 2)]), \
                um.patch.object(TH, '_krpc',
                                side_effect=[None, {b'values': [b'x' * 6],
                                                    b'nodes': b''}]):
            self.assertEqual(TH._dht_peers('a' * 40), 1)

    def test_nodo_autovisto_no_reencola(self):
        """Un nodo que se devuelve a sí mismo en `nodes` no se re-encola
        (rama a not in seen -> no append)."""
        import unittest.mock as um
        import socket as so
        import struct as st
        nb = b'n' * 20 + so.inet_aton('1.1.1.1') + st.pack('!H', 1)
        with um.patch.object(TH.socket, 'socket', return_value=um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes',
                                return_value=[('1.1.1.1', 1)]), \
                um.patch.object(TH, '_krpc',
                                return_value={b'values': [], b'nodes': nb}) as k:
            self.assertEqual(TH._dht_peers('a' * 40), 0)
        self.assertEqual(k.call_count, 1)

    def test_close_fallando_no_propaga(self):
        import unittest.mock as um
        m = um.MagicMock()
        m.close.side_effect = OSError
        with um.patch.object(TH.socket, 'socket', return_value=m), \
                um.patch.object(TH, '_dht_nodes', return_value=[('1.1.1.1', 1)]), \
                um.patch.object(TH, '_krpc',
                                return_value={b'values': [b'x' * 6]}):
            self.assertEqual(TH._dht_peers('a' * 40), 1)


class TestScrapeEdges(unittest.TestCase):
    """_scrape_tracker: respuestas truncadas / tx ajeno -> {}."""
    import struct as _st

    def _sock(self, r0f, r1f=None):
        st = self._st
        class S:
            def __init__(self, *a):
                self._n = 0
                self._tx = 0
            def settimeout(self, t): pass
            def close(self): pass
            def sendto(self, b, dst):
                self._tx = st.unpack('!I', b[12:16])[0]
                self._n += 1
            def recvfrom(self, n):
                return (r0f(self._tx) if self._n == 1 else r1f(self._tx)), None
        return S

    def _run(self, sock):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', sock), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            return TH._scrape_tracker('t.test', 1, ['a' * 40])

    def test_connect_respuesta_corta_vacio(self):
        self.assertEqual(self._run(self._sock(lambda t: b'xx')), {})

    def test_connect_tx_ajeno_vacio(self):
        st = self._st
        self.assertEqual(
            self._run(self._sock(lambda t: st.pack('!IIQ', 0, t + 1, 9))), {})

    def test_scrape_tx2_ajeno_vacio(self):
        st = self._st
        def r0(t):
            return st.pack('!IIQ', 0, t, 9)
        def r1(t):
            return st.pack('!II', 2, t + 1) + st.pack('!III', 5, 0, 0)
        self.assertEqual(self._run(self._sock(r0, r1)), {})


class TestAnnounceUdpEdges(unittest.TestCase):
    """_announce_udp: deny/cortos en cada fase del handshake."""
    import struct as _st

    def _sock(self, r0f, r1f=None, close_raises=False):
        st = self._st
        class S:
            def __init__(self, *a):
                self._n = 0
                self._tx = 0
            def settimeout(self, t): pass
            def close(self):
                if close_raises:
                    raise OSError()
            def sendto(self, b, dst):
                self._tx = st.unpack('!I', b[12:16])[0]
                self._n += 1
            def recvfrom(self, n):
                return (r0f(self._tx) if self._n == 1 else r1f(self._tx)), None
        return S

    def _run(self, sock):
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', sock), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            return TH._announce_udp('t.test', 1, 'a' * 40)

    def test_connect_corto_none(self):
        self.assertIsNone(self._run(self._sock(lambda t: b'x')))

    def test_connect_deny(self):
        st = self._st
        self.assertEqual(
            self._run(self._sock(lambda t: st.pack('!IIQ', 3, t, 0))), 'deny')

    def test_connect_tx_ajeno_none(self):
        st = self._st
        self.assertIsNone(
            self._run(self._sock(lambda t: st.pack('!IIQ', 0, t + 1, 9))))

    def test_announce_corto_none(self):
        st = self._st
        def r0(t):
            return st.pack('!IIQ', 0, t, 9)
        self.assertIsNone(self._run(self._sock(r0, lambda t: b'y' * 10)))

    def test_close_fallando_devuelve_resultado(self):
        st = self._st
        def r0(t):
            return st.pack('!IIQ', 0, t, 9)
        def r1(t):
            return st.pack('!II', 1, t) + st.pack('!III', 0, 2, 5)
        r = self._run(self._sock(r0, r1, close_raises=True))
        self.assertEqual(r['seeds'], 5)


class TestAnnounceHttpEdges(unittest.TestCase):
    def test_no_dict_none(self):
        import unittest.mock as um
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n): return TH._benc(5)     # int, no dict
        with um.patch('urllib.request.urlopen', return_value=R()):
            self.assertIsNone(TH._announce_http('https://t/a', 'a' * 40))

    def test_peers_lista_no_compacta(self):
        import unittest.mock as um
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n):
                return TH._benc({b'complete': 4, b'incomplete': 1,
                                 b'peers': [b'x', b'y']})
        with um.patch('urllib.request.urlopen', return_value=R()):
            r = TH._announce_http('https://t/a', 'a' * 40)
        self.assertEqual(r, {'seeds': 4, 'leechers': 1, 'peers': 2})


class TestAnnounceHintsEdges(unittest.TestCase):
    def test_url_irreconocible_salta(self):
        import unittest.mock as um
        with um.patch.object(TH, '_announce_udp') as au, \
                um.patch.object(TH, '_announce_http') as ah:
            r = TH._announce_hints('a' * 40, ['ftp://x', 'no-es-url'])
        self.assertEqual(r, 'unknown')
        au.assert_not_called()
        ah.assert_not_called()

    def test_http_usa_announce_http(self):
        import unittest.mock as um
        with um.patch.object(TH, '_announce_http',
                             return_value={'seeds': 1, 'leechers': 0,
                                           'peers': 0}) as ah:
            r = TH._announce_hints('a' * 40, ['https://t.test/announce'])
        self.assertEqual(r, 'alive')
        ah.assert_called_once()

    def test_deny_cuenta_como_respuesta(self):
        import unittest.mock as um
        with um.patch.object(TH, '_announce_udp', side_effect=['deny', 'deny']):
            r = TH._announce_hints('a' * 40, ['udp://t1:1/x', 'udp://t2:2/x'])
        self.assertEqual(r, 'dead')                  # 2 respondieron: muerto


class TestScrapeHashes(unittest.TestCase):
    """_scrape_hashes: chunks + trackers en orden hasta que uno responde."""

    def test_primer_tracker_vacio_pasa_al_segundo(self):
        import unittest.mock as um
        with um.patch.object(TH, '_scrape_tracker',
                             side_effect=[{}, {'a' * 40: 7}]) as sc:
            out = TH._scrape_hashes(['a' * 40])
        self.assertEqual(out, {'a' * 40: 7})
        self.assertEqual(sc.call_count, 2)

    def test_chunking_20_en_20(self):
        import unittest.mock as um
        hs = ['%040x' % i for i in range(21)]
        with um.patch.object(TH, '_scrape_tracker', return_value={}) as sc:
            TH._scrape_hashes(hs)
        self.assertEqual(sc.call_count, 2 * len(TH._TRACKERS))  # 2 chunks x 4 trackers

    def test_todos_muertos_vacio(self):
        import unittest.mock as um
        with um.patch.object(TH, '_scrape_tracker', return_value={}):
            self.assertEqual(TH._scrape_hashes(['a' * 40]), {})


class TestScrapeSubmitEdges(unittest.TestCase):
    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def test_budget_90s_break(self):
        """El budget de 90s corta el loop DHT: los hashes restantes quedan
        sin veredicto (retry en la próxima pasada)."""
        import unittest.mock as um
        hs = ['%040x' % i for i in range(3)]
        with um.patch.object(TH, '_scrape_hashes', return_value={}), \
                um.patch.object(TH, '_dht_peers', return_value=0), \
                um.patch.object(TH, '_seed_save'), \
                um.patch.object(TH, 'time') as t:
            t.time.side_effect = [1000.0, 1001.0, 1095.0]
            TH._scrape_submit([(h, []) for h in hs])
        self.assertIn(hs[0], TH._SEED)               # 1º: dentro del budget
        self.assertNotIn(hs[1], TH._SEED)            # 2º en adelante: break

    def test_purga_mas_de_4000(self):
        import unittest.mock as um
        TH._SEED.update({'%040d' % i: (1, float(i)) for i in range(4100)})
        with um.patch.object(TH, '_scrape_hashes', return_value={}), \
                um.patch.object(TH, '_dht_peers', return_value=0), \
                um.patch.object(TH, '_seed_save'):
            TH._scrape_submit([('f' * 40, [])])
        self.assertLess(len(TH._SEED), 2500)          # mitad vieja purgada
        self.assertIn('f' * 40, TH._SEED)             # veredicto nuevo queda


class TestHashDeadEdges(unittest.TestCase):
    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def test_sin_infohash_false(self):
        self.assertFalse(TH._hash_dead({'url': 'http://x'}))

    def test_announce_dead_siempre_fuera(self):
        TH._SEED['b' * 40] = (-2, time.time())
        self.assertTrue(TH._hash_dead(
            {'infoHash': 'b' * 40, 'sources': ['tracker:udp://x']}))


class TestSeedPersist(unittest.TestCase):
    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def test_load_corrupto_no_muere(self):
        import tempfile
        import os
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
        import tempfile
        import os
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
        import tempfile
        import os
        import json
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

    def test_load_fichero_ausente_no_muere(self):
        TH._seed_load('no/existe/ruta-imposible.json')      # early return
        self.assertEqual(TH._SEED, {})

    def test_save_fallo_disco_tragado(self):
        import unittest.mock as um
        TH._LAST_SAVE[0] = 0
        with um.patch('tempfile.mkstemp', side_effect=OSError):
            TH._seed_save('x')                               # no propaga

    def test_save_throttle_60s(self):
        import tempfile
        import os
        import json
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

    def test_save_borde_60s_escribe(self):
        """age == 60 -> ya no throttles (Lt@62: <= lo saltaría; 60+1@62)."""
        import tempfile
        import os
        from freezegun import freeze_time
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 's.json')
            with freeze_time('2025-06-01 12:00:00'):
                TH._LAST_SAVE[0] = time.time() - 60   # justo en el borde
                TH._SEED['b' * 40] = (3, time.time())
                TH._seed_save(p)
            self.assertTrue(os.path.exists(p))

    def test_save_sin_dirname_usa_cwd(self):
        """dirname(path) or '.' (Or@69: and -> '' rompe mkstemp)."""
        import tempfile
        import os
        import json
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as td:
            try:
                os.chdir(td)
                TH._LAST_SAVE[0] = 0
                TH._SEED['c' * 40] = (7, time.time())
                TH._seed_save('rel.json')            # sin directorio -> '.'
                self.assertTrue(os.path.exists('rel.json'))
                with open('rel.json') as f:
                    self.assertEqual(json.load(f)['c' * 40][0], 7)
            finally:
                os.chdir(cwd)

    def test_constantes_operativas(self):
        """Contrato operacional: trackers, chunk, TTL y timeout (@30-35)."""
        self.assertEqual(TH._TRACKERS,
                         [('open.demonii.com', 1337),
                          ('tracker.torrent.eu.org', 451),
                          ('open.stealth.si', 80),
                          ('tracker.dler.org', 6969)])
        self.assertEqual(TH._CHUNK, 20)
        self.assertEqual(TH._TTL, 21600)             # 6*3600
        self.assertEqual(TH._TMO, 4)
        self.assertEqual(TH._DHT_BOOT,
                         [('router.bittorrent.com', 6881),
                          ('router.utorrent.com', 6881),
                          ('dht.transmissionbt.com', 6881)])
        self.assertEqual(len(TH._NID), 20)           # node id 20B


class TestWireBytes(unittest.TestCase):
    """Los fakes no validan lo enviado: un byte mal y el tracker real nos
    ignora — hay que asertar el protocolo en sendto."""

    def test_scrape_connect_magic_y_accion(self):
        import struct
        import unittest.mock as um
        sent = []
        class S:
            def __init__(self):
                self.n = 0
                self.recv_n = []
            def settimeout(self, t): self.t = t
            def sendto(self, b, dst):
                sent.append(b)
                self.n += 1
            def recvfrom(self, n):
                self.recv_n.append(n)
                if self.n == 1:
                    return struct.pack('!IIQ', 0,
                                       struct.unpack('!I', sent[0][12:16])[0],
                                       99), None
                return struct.pack('!II', 2,
                                   struct.unpack('!I', sent[1][12:16])[0]) + \
                    struct.pack('!III', 5, 0, 0), None
            def close(self): pass
        s = S()
        with um.patch.object(TH.socket, 'socket', return_value=s), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._scrape_tracker('t', 1, ['a' * 40])
        self.assertEqual(s.recv_n, [2048, 65535])    # tamaños del protocolo
        magic, act, _ = struct.unpack('!QII', sent[0][:16])
        self.assertEqual(magic, 0x41727101980)       # BEP15 connect
        self.assertEqual(act, 0)
        self.assertEqual(struct.unpack('!QII', sent[1][:16])[1], 2)   # scrape
        self.assertEqual(r, {'a' * 40: 5})

    def test_scrape_connect_respuesta_corta_vacia(self):
        """len(r0) < 16 -> {} (16+1: 16 justo debe parsear)."""
        import struct
        import unittest.mock as um
        class S:
            def settimeout(self, t): pass
            def sendto(self, b, dst): self.tx = struct.unpack('!I', b[12:16])[0]
            def recvfrom(self, n): return b'\x00' * 15, None
            def close(self): pass
        with um.patch.object(TH.socket, 'socket', return_value=S()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            self.assertEqual(TH._scrape_tracker('t', 1, ['a' * 40]), {})

    def test_scrape_tx2_ajeno_vacio(self):
        """rtx2 != tx2 -> {} (echo de transacción)."""
        import struct
        import unittest.mock as um
        class S:
            def __init__(self): self.n = 0
            def settimeout(self, t): pass
            def sendto(self, b, dst):
                self.n += 1
                self.tx = struct.unpack('!I', b[12:16])[0]
            def recvfrom(self, n):
                if self.n == 1:
                    return struct.pack('!IIQ', 0, self.tx, 7), None
                return struct.pack('!II', 2, self.tx + 1) + b'\x00' * 12, None
            def close(self): pass
        with um.patch.object(TH.socket, 'socket', return_value=S()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            self.assertEqual(TH._scrape_tracker('t', 1, ['a' * 40]), {})

    def test_announce_udp_accion_y_puerto(self):
        """pkt announce: action=1, puerto 6881 en el tail."""
        import struct
        import unittest.mock as um
        sent = []
        class S:
            def __init__(self): self.n = 0
            def settimeout(self, t): pass
            def sendto(self, b, dst):
                sent.append(b)
                self.n += 1
            def recvfrom(self, n):
                if self.n == 1:
                    return struct.pack('!IIQ', 0,
                                       struct.unpack('!I', sent[0][12:16])[0], 5), None
                return struct.pack('!II', 1,
                                   struct.unpack('!I', sent[1][12:16])[0]) + \
                    struct.pack('!III', 0, 2, 9) + b'\x00' * 6, None
            def close(self): pass
        with um.patch.object(TH.socket, 'socket', return_value=S()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._announce_udp('t', 1, 'a' * 40)
        self.assertEqual(struct.unpack('!QII', sent[1][:16])[1], 1)   # announce
        self.assertEqual(struct.unpack('!H', sent[1][-2:])[0], 6881)  # port
        self.assertEqual(r['seeds'], 9)
        self.assertEqual(r['peers'], 1)              # (len-20)//6

    def test_announce_respuesta_20_exacta(self):
        """len(r) < 20 -> None; exactamente 20 parsea (Lt: <21 lo rompería)."""
        import struct
        import unittest.mock as um
        sent = []
        class S:
            def __init__(self): self.n = 0
            def settimeout(self, t): pass
            def sendto(self, b, dst): sent.append(b); self.n += 1
            def recvfrom(self, n):
                if self.n == 1:
                    return struct.pack('!IIQ', 0,
                                       struct.unpack('!I', sent[0][12:16])[0], 5), None
                return struct.pack('!II', 1,
                                   struct.unpack('!I', sent[1][12:16])[0]) + \
                    struct.pack('!III', 0, 0, 3), None
            def close(self): pass
        with um.patch.object(TH.socket, 'socket', return_value=S()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._announce_udp('t', 1, 'a' * 40)
        self.assertEqual(r['seeds'], 3)
        self.assertEqual(r['peers'], 0)              # (20-20)//6

    def test_krpc_tid_2b_y_r(self):
        """tid de 2B y devuelve r no-vacío (2+1@tid, Or@r)."""
        sent = []
        class S:
            def sendto(self, b, dst): sent.append(b)
            def recvfrom(self, n):
                d, _ = TH._bdec(sent[0])
                return TH._benc({b't': d[b't'], b'y': b'r',
                                 b'r': {b'ip': b'x'}}), None
        r = TH._krpc(S(), ('1.2.3.4', 1), 'ping', {})
        d, _ = TH._bdec(sent[0])
        self.assertEqual(len(d[b't']), 2)
        self.assertEqual(r, {b'ip': b'x'})

    def test_krpc_recvfrom_65535(self):
        seen = []
        class S:
            def sendto(self, b, dst): pass
            def recvfrom(self, n):
                seen.append(n)
                return TH._benc({b't': b'zz', b'y': b'r', b'r': {}}), None
        TH._krpc(S(), ('1.2.3.4', 1), 'ping', {})
        self.assertEqual(seen, [65535])


class TestDhtNodos(unittest.TestCase):
    def setUp(self):
        self._nodes = TH._DHT_NODES
        TH._DHT_NODES = []

    def tearDown(self):
        TH._DHT_NODES = self._nodes

    def _node(self, ip, port):
        import socket as so
        import struct as st
        return b'n' * 20 + so.inet_aton(ip) + st.pack('!H', port)

    def test_nodos_26b_dos_registros(self):
        """Registro nodo = 26B (20 id + 4 ip + 2 port) — 26+1 parte records."""
        import unittest.mock as um
        nb = self._node('1.1.1.1', 6881) + self._node('2.2.2.2', 51413)
        with um.patch.object(TH, '_krpc',
                             side_effect=[{b'nodes': nb}, {}, {}]) as k:
            out = TH._dht_nodes(um.MagicMock())
        self.assertEqual(out, [('1.1.1.1', 6881), ('2.2.2.2', 51413)])
        self.assertEqual(k.call_args[0][4], 3)       # timeout=3 por nodo

    def test_nodos_cap_64(self):
        """nodes[:64] — LRU acotado (64+1)."""
        import unittest.mock as um
        nb = b''.join(self._node(f'10.0.0.{i % 250 + 1}', i) for i in range(70))
        with um.patch.object(TH, '_krpc', return_value={b'nodes': nb}):
            out = TH._dht_nodes(um.MagicMock())
        self.assertEqual(len(out), 64)

    def test_dht_peers_cap_10_y_nodes(self):
        """asked < cap (10) y cada nodo 26B aprendido entra en todo."""
        import unittest.mock as um
        nb = self._node('9.9.9.9', 1) * 1
        calls = []
        def krpc(s, addr, q, args, timeout):
            calls.append(addr)
            return {b'values': [], b'nodes': nb}
        nodes = [(f'10.0.0.{i}', i) for i in range(15)]
        with um.patch.object(TH.socket, 'socket', lambda *a: um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes', return_value=nodes), \
                um.patch.object(TH, '_krpc', side_effect=krpc):
            self.assertEqual(TH._dht_peers('a' * 40), 0)
        self.assertEqual(len(calls), 10)             # cap=10 para en seco

    def test_dht_peers_nodos_26b_y_puerto(self):
        """Nodos aprendidos: records de 26B con su puerto real (26+1@189/191)."""
        import unittest.mock as um
        nb = self._node('9.9.9.9', 1111) + self._node('8.8.8.8', 2222)
        calls = []
        def krpc(s, addr, q, args, timeout):
            calls.append(addr)
            return {b'values': [b'p' * 6], b'nodes': nb}   # peer -> para ya
        with um.patch.object(TH.socket, 'socket', lambda *a: um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes', return_value=[('1.2.3.4', 1)]), \
                um.patch.object(TH, '_krpc', side_effect=krpc):
            self.assertEqual(TH._dht_peers('a' * 40), 1)
        self.assertEqual(calls, [('1.2.3.4', 1)])    # para al hallar peer
        # el aprendizaje de nodos lo valida _dht_nodes con 2 records:
        TH._DHT_NODES = []
        def krpc2(s, addr, q, args, timeout):
            calls.append(addr)
            return {b'values': [], b'nodes': nb}
        with um.patch.object(TH.socket, 'socket', lambda *a: um.MagicMock()), \
                um.patch.object(TH, '_dht_nodes', return_value=[('1.2.3.4', 1)]), \
                um.patch.object(TH, '_krpc', side_effect=krpc2):
            TH._dht_peers('a' * 40)
        self.assertIn(('9.9.9.9', 1111), calls)
        self.assertIn(('8.8.8.8', 2222), calls)


class TestAnnounceHttpProto(unittest.TestCase):
    def _open(self, data):
        import unittest.mock as um
        req = []
        resp = um.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.side_effect = lambda n: (req.append(('n', n)), data)[1]
        def urlopen(r, timeout=None):
            req.append((r.full_url, timeout))
            return resp
        return req, urlopen

    def test_sep_query_y_timeout(self):
        """'?' in url -> '&' si ya hay query (In@292, Add@339); timeout=5;
        read(65536)."""
        d = TH._benc({b'complete': 4, b'incomplete': 1, b'peers': b''})
        req, urlopen = self._open(d)
        import unittest.mock as um
        with um.patch('urllib.request.urlopen', urlopen):
            TH._announce_http('http://t/ann', 'a' * 40)
            self.assertIn('?info_hash=', req[0][0])
            self.assertEqual(req[0][1], 5)
            self.assertIn(('n', 65536), req)
            TH._announce_http('http://t/ann?x=1', 'a' * 40)
            self.assertIn('&info_hash=', req[-2][0])

    def test_peers_compactos_6b(self):
        """len(peers)//6 — 12B = 2 peers (6+1)."""
        d = TH._benc({b'complete': 0, b'incomplete': 0,
                      b'peers': b'\x00' * 12})
        req, urlopen = self._open(d)
        import unittest.mock as um
        with um.patch('urllib.request.urlopen', urlopen):
            r = TH._announce_http('http://t/ann', 'a' * 40)
        self.assertEqual(r['peers'], 2)


class TestAnnounceHintsCap(unittest.TestCase):
    def test_cap_6_y_puerto_defecto(self):
        """urls[:6] cap; 'udp://host' sin puerto -> 80 (Or/80+1@322)."""
        import unittest.mock as um
        urls = [f'udp://h{i}' for i in range(8)]
        calls = []
        with um.patch.object(TH, '_announce_udp',
                             side_effect=lambda h, p, i, *a: calls.append((h, p)) or None):
            self.assertEqual(TH._announce_hints('a' * 40, urls), 'unknown')
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[0], ('h0', 80))

    def test_url_mal_formada_salta(self):
        """regex udp|http no casa -> continue (tracker: sin esquema)."""
        import unittest.mock as um
        with um.patch.object(TH, '_announce_udp') as au, \
                um.patch.object(TH, '_announce_http') as ah:
            self.assertEqual(TH._announce_hints('a' * 40, ['noschema']), 'unknown')
            au.assert_not_called(); ah.assert_not_called()


class TestScrapeSubmitVeredictos(unittest.TestCase):
    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def test_seed_cero_no_es_vivo(self):
        """sd > 0 -> veredicto vivo (Gt@357: >= metería los 0-seed)."""
        import unittest.mock as um
        h = 'a' * 40
        with um.patch.object(TH, '_scrape_hashes', return_value={h: 0}), \
                um.patch.object(TH, '_announce_hints', return_value='unknown'), \
                um.patch.object(TH, '_dht_peers', return_value=0), \
                um.patch.object(TH, '_seed_save'):
            TH._scrape_submit([(h, [])])
        self.assertEqual(TH._SEED[h][0], -1)         # no vivo: dead-unknown

    def test_purga_mitad_mas_vieja(self):
        """len(_SEED) > 4000 -> poda a la mitad (4000+1/Gt@377)."""
        import unittest.mock as um
        for i in range(4001):
            TH._SEED[f'{i:040x}'] = (1, float(i))
        h = 'a' * 40
        with um.patch.object(TH, '_scrape_hashes', return_value={h: 5}), \
                um.patch.object(TH, '_seed_save'):
            TH._scrape_submit([(h, [])])
        self.assertLessEqual(len(TH._SEED), 2002)

    def test_ttl_borde_exacto_conocido(self):
        """age == _TTL aún fresco (Gt@388: >= lo daría por caducado)."""
        from freezegun import freeze_time
        with freeze_time('2025-06-01 12:00:00'):
            TH._SEED['a' * 40] = (9, time.time() - TH._TTL)
            self.assertEqual(TH._hash_known('a' * 40), 9)

    def test_ttl_pasado_desconocido(self):
        from freezegun import freeze_time
        with freeze_time('2025-06-01 12:00:00'):
            TH._SEED['a' * 40] = (9, time.time() - TH._TTL - 1)
            self.assertIsNone(TH._hash_known('a' * 40))


class TestHashFilterProto(unittest.TestCase):
    """_hash_filter: solo hash 40-hex válido y sources 'tracker:' -> urls."""

    def _filt(self, streams):
        return TH._hash_filter(streams)

    def test_tracker_url_8_prefix(self):
        """u[8:] quita 'tracker:' (8+1@417: perdería la 'u' de udp)."""
        s = {'infoHash': 'a' * 40,
             'sources': ['tracker:udp://h1:1337', 'tracker:http://h2']}
        dead, todo = self._filt([s])
        self.assertEqual(todo, [('a' * 40, ['udp://h1:1337', 'http://h2'])])

    def test_sources_no_str_no_explotan(self):
        """isinstance(u,str) and startswith (And@418: or -> crash en int)."""
        s = {'infoHash': 'a' * 40, 'sources': [123, 'tracker:udp://h']}
        dead, todo = self._filt([s])
        self.assertEqual(todo, [('a' * 40, ['udp://h'])])

    def test_hash_longitud_40(self):
        """len(h) != 40 -> skip (Or@411/And@415)."""
        dead, todo = self._filt([{'infoHash': 'a' * 39},
                                 {'infoHash': 'a' * 41},
                                 {'infoHash': 'a' * 40}])
        self.assertEqual(len(todo), 1)


class TestLotesHealth(unittest.TestCase):
    def test_lotes_de_140(self):
        """min(len,140) por petición y lote _CHUNK*7=140 (140+1/7+1/Add@446)."""
        import unittest.mock as um
        enviados = []
        with um.patch.object(TH._POOL_BG, 'submit',
                             lambda f, *a: enviados.append(a[0])):
            streams = [{'infoHash': f'{i:040x}'} for i in range(200)]
            TH._hash_health(streams)
        self.assertEqual([len(x) for x in enviados], [140])   # resto: próxima req


class TestScrapeChunks(unittest.TestCase):
    def test_chunks_de_20(self):
        """hashes[i:i+_CHUNK] — 21 hashes = 2 scrapes (Add@339: i-CHUNK vacío)."""
        import unittest.mock as um
        chunks = []
        with um.patch.object(TH, '_scrape_tracker',
                             side_effect=lambda h, p, c, *a: chunks.append(c) or {'x': 1}):
            TH._scrape_hashes([f'{i:040x}' for i in range(21)])
        self.assertEqual([len(c) for c in chunks], [20, 1])


class TestProtocoloConst(unittest.TestCase):
    def test_peer_id_20b(self):
        """_PID = '-CF0001-' + 12B random = 20B (12+1@241, Add@242)."""
        self.assertEqual(len(TH._PID), 20)
        self.assertTrue(TH._PID.startswith(b'-CF0001-'))

    def test_tx_randint_2e31(self):
        """tx = random.randint(0, 2**31) — BEP15 (31+1@{209,218,252,260,263})."""
        import unittest.mock as um
        import struct
        calls = []
        class S:
            def __init__(self): self.n = 0
            def settimeout(self, t): pass
            def sendto(self, b, dst): self.n += 1
            def recvfrom(self, n):
                if self.n == 1:
                    return struct.pack('!IIQ', 0, 0, 1), None
                return struct.pack('!II', 2, 0), None
            def close(self): pass
        with um.patch.object(TH.random, 'randint',
                             side_effect=lambda a, b: calls.append((a, b)) or 0), \
                um.patch.object(TH.socket, 'socket', return_value=S()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            TH._scrape_tracker('t', 1, ['a' * 40])
            TH._announce_udp('t', 1, 'a' * 40)
        self.assertTrue(calls)
        self.assertTrue(all(c == (0, 2**31) for c in calls))

    def test_defaults_firma(self):
        """Timeouts/caps de firma son contrato (3+1@129/164, 4+1@245)."""
        import inspect
        self.assertEqual(inspect.signature(TH._krpc).parameters['timeout'].default, 3)
        self.assertEqual(inspect.signature(TH._dht_peers).parameters['cap'].default, 10)
        self.assertEqual(inspect.signature(TH._dht_peers).parameters['timeout'].default, 3)
        self.assertEqual(inspect.signature(TH._announce_udp).parameters['timeout'].default, 4)
        self.assertEqual(inspect.signature(TH._announce_hints).parameters['cap'].default, 6)

    def test_announce_connect_magic_y_recv(self):
        """Connect BEP15 en announce: magic + recvfrom(2048/65535) (2048/65535+1)."""
        import struct
        import unittest.mock as um
        sent, recv_n = [], []
        class S:
            def __init__(self): self.n = 0
            def settimeout(self, t): pass
            def sendto(self, b, dst): sent.append(b); self.n += 1
            def recvfrom(self, n):
                recv_n.append(n)
                if self.n == 1:
                    return struct.pack('!IIQ', 0,
                                       struct.unpack('!I', sent[0][12:16])[0], 5), None
                return struct.pack('!II', 1,
                                   struct.unpack('!I', sent[1][12:16])[0]) + \
                    struct.pack('!III', 0, 0, 3), None
            def close(self): pass
        with um.patch.object(TH.socket, 'socket', return_value=S()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            TH._announce_udp('t', 1, 'a' * 40)
        self.assertEqual(struct.unpack('!QII', sent[0][:16])[0], 0x41727101980)
        self.assertEqual(recv_n, [2048, 65535])


class TestSinkYFiltro(unittest.TestCase):
    def setUp(self):
        self._seed = dict(TH._SEED)
        TH._SEED.clear()

    def tearDown(self):
        TH._SEED.clear()
        TH._SEED.update(self._seed)

    def test_conocido_fresco_no_reencola(self):
        """known is None and h not in seen (And@415: or -> recola conocidos)."""
        h = 'a' * 40
        TH._SEED[h] = (5, time.time())               # veredicto fresco
        dead, todo = TH._hash_filter([{'infoHash': h}])
        self.assertEqual(todo, [])

    def test_dead_sin_sources_no_se_hunde(self):
        """sources and known in (-1,-2) (And@427: or hundiría sin hints)."""
        h = 'a' * 40
        TH._SEED[h] = (-1, time.time())
        streams = [{'infoHash': h, 'x': 1}, {'infoHash': 'b' * 40}]
        TH._hash_sink(streams)
        self.assertEqual(streams[0]['infoHash'], h)  # sin sources: no se mueve

    def test_menos2_si_se_hunde(self):
        """known in (-1,-2): -2 (announce-dead) también se hunde (2+1@428)."""
        h = 'a' * 40
        TH._SEED[h] = (-2, time.time())
        streams = [{'infoHash': h, 'sources': ['tracker:u']},
                   {'infoHash': 'b' * 40}]
        TH._hash_sink(streams)
        self.assertEqual(streams[-1]['infoHash'], h)

    def test_purga_borde_4000_no_poda(self):
        """len(_SEED) > 4000 — exactamente 4000 no purga (Gt@377: >=)."""
        import unittest.mock as um
        for i in range(4000):
            TH._SEED[f'{i:040x}'] = (1, float(i))
        h = 'f' * 40
        with um.patch.object(TH, '_scrape_hashes', return_value={h: 5}), \
                um.patch.object(TH, '_seed_save'):
            TH._scrape_submit([(h, [])])
        self.assertEqual(len(TH._SEED), 4001)        # creció, sin poda












class TestWireFormatMut(unittest.TestCase):
    """Mutantes wire-format UDP/announce — padding, close-spy, randint bounds."""

    def _scrape_sock(self, pad=b'', closed_flag=False):
        import struct
        class FakeSock:
            def __init__(self, *a):
                self._n = 0
                self.closed = False
            def settimeout(self, t): pass
            def close(self): self.closed = True
            def sendto(self, b, dst):
                self._tx = struct.unpack('!I', b[12:16])[0]
                self._n += 1
            def recvfrom(self, n):
                if self._n == 1:
                    return struct.pack('!IIQ', 0, self._tx, 12345) + pad, None
                return struct.pack('!II', 2, self._tx) + \
                    struct.pack('!III', 9, 0, 0) * 2, None
        return FakeSock

    def test_scrape_connect_padding_16(self):
        """Connect-response >16B: r0[:16] parsea igual (16+1@214 -> r0[:17]
        hace unpack('!IIQ', 17B) -> error -> {})."""
        import unittest.mock as um
        h1 = 'aa' * 20
        with um.patch.object(TH.socket, 'socket', self._scrape_sock(pad=b'JUNK')), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._scrape_tracker('t.test', 1, [h1])
        self.assertEqual(r.get(h1), 9)

    def test_scrape_cierra_socket(self):
        """El finally cierra s (IsNot@229: `is not None` -> `is None`
        dejaría el socket abierto)."""
        import unittest.mock as um
        cls = self._scrape_sock()
        inst = cls()
        with um.patch.object(TH.socket, 'socket', return_value=inst), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            TH._scrape_tracker('t.test', 1, ['aa' * 20])
        self.assertTrue(inst.closed)

    def _announce_sock(self, pad=b''):
        import struct
        class FakeSock:
            def __init__(self, *a):
                self._n = 0
                self.closed = False
            def settimeout(self, t): pass
            def close(self): self.closed = True
            def sendto(self, b, dst):
                self._tx = struct.unpack('!I', b[12:16])[0]
                self._n += 1
            def recvfrom(self, n):
                if self._n == 1:
                    return struct.pack('!IIQ', 0, self._tx, 999) + pad, None
                return struct.pack('!II', 1, self._tx) + \
                    struct.pack('!III', 1800, 3, 7) + b'\x00' * 12, None
        return FakeSock

    def test_announce_connect_padding_16(self):
        """Igual que scrape: r0[:16] sobre respuesta de 20B (16+1@257)."""
        import unittest.mock as um
        with um.patch.object(TH.socket, 'socket', self._announce_sock(pad=b'XXXX')), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            r = TH._announce_udp('t.test', 1, 'a' * 40)
        self.assertEqual(r, {'seeds': 7, 'leechers': 3, 'peers': 2})

    def test_announce_cierra_socket(self):
        """El finally cierra s (IsNot@277)."""
        import unittest.mock as um
        inst = self._announce_sock()()
        with um.patch.object(TH.socket, 'socket', return_value=inst), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'):
            TH._announce_udp('t.test', 1, 'a' * 40)
        self.assertTrue(inst.closed)

    def test_announce_randint_bounds(self):
        """tx/tx2/numwant siempre randint(0, 2**31) (31+1@260 y 31+1@263:
        un bound 2**32 rompe el handshake en trackers estrictos)."""
        import unittest.mock as um
        calls = []
        def fake_randint(a, b):
            calls.append((a, b))
            return 1
        with um.patch.object(TH.socket, 'socket', self._announce_sock()), \
                um.patch.object(TH.socket, 'gethostbyname', return_value='1.2.3.4'), \
                um.patch.object(TH.random, 'randint', fake_randint):
            TH._announce_udp('t.test', 1, 'a' * 40)
        self.assertTrue(calls)
        for c in calls:
            self.assertEqual(c, (0, 2**31))


class TestSeedSaveMut(unittest.TestCase):
    def test_seed_save_basename(self):
        """dirname('seed.json')='' -> dir='.' (Or@69: `and` daría dir=''
        y mkstemp fallaría -> no se escribe nada)."""
        import os
        import tempfile
        old_last = TH._LAST_SAVE[0]
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as td:
            os.chdir(td)
            try:
                TH._LAST_SAVE[0] = 0.0
                TH._seed_save('seed.json')
                self.assertTrue(os.path.exists('seed.json'))
            finally:
                os.chdir(cwd)
                TH._LAST_SAVE[0] = old_last


class TestScrapeSubmitMut(unittest.TestCase):
    def test_zero_hashes_se_anuncian(self):
        """res.get(h,0)==0 -> zero -> hinted -> announce (Eq@358: `!=0`
        metería los vivos en `zero` y saltaría el announce del hash real)."""
        import unittest.mock as um
        h = 'c' * 40
        ann_calls = []
        old_seed = dict(TH._SEED)
        try:
            TH._SEED.clear()
            with um.patch.object(TH, '_scrape_hashes', return_value={h: 0}), \
                    um.patch.object(TH, '_announce_hints',
                                    side_effect=lambda *a: (ann_calls.append(a), 'alive')[1]), \
                    um.patch.object(TH, '_dht_peers',
                                    side_effect=AssertionError('no DHT: tiene hints')), \
                    um.patch.object(TH, '_seed_save'):
                TH._scrape_submit([(h, ['tracker:http://t/a'])])
            self.assertEqual(len(ann_calls), 1)
            self.assertEqual(TH._SEED[h][0], 99)      # 'alive' -> 99
        finally:
            TH._SEED.clear()
            TH._SEED.update(old_seed)


class TestCfgGate(unittest.TestCase):
    """_hash_health: gate `not streams or not _cfgget('torrent_health', True)`."""

    def test_ausente_default_activo(self):
        """Sin la clave en CFG -> default True -> filtra muertos (True@438:
        default False lo dejaría todo quieto)."""
        import conflux.core.state as st
        h = 'd' * 40
        old = dict(TH._SEED)
        try:
            TH._SEED.clear()
            TH._SEED[h] = (-1, time.time())
            had = 'torrent_health' in st.CFG
            old_v = st.CFG.get('torrent_health')
            st.CFG.pop('torrent_health', None)
            try:
                streams = [{'infoHash': h}]
                TH._hash_health(streams)
                self.assertEqual(streams, [])        # activo: el muerto sale
            finally:
                if had:
                    st.CFG['torrent_health'] = old_v
        finally:
            TH._SEED.clear()
            TH._SEED.update(old)

    def test_desactivado_conserva_muertos(self):
        """torrent_health=False -> return temprano: los muertos SE QUEDAN
        (Or@438: `and` no retornaba y los filtraba igualmente)."""
        import conflux.core.state as st
        h = 'e' * 40
        old = dict(TH._SEED)
        try:
            TH._SEED.clear()
            TH._SEED[h] = (-1, time.time())
            had = 'torrent_health' in st.CFG
            old_v = st.CFG.get('torrent_health')
            st.CFG['torrent_health'] = False
            try:
                streams = [{'infoHash': h}]
                TH._hash_health(streams)
                self.assertEqual(len(streams), 1)    # desactivado: se conserva
            finally:
                if had:
                    st.CFG['torrent_health'] = old_v
                else:
                    st.CFG.pop('torrent_health', None)
        finally:
            TH._SEED.clear()
            TH._SEED.update(old)


class TestDhtBudget(unittest.TestCase):
    """`time.time() - t0 > 90` corta el loop DHT (90+1@367, Gt@367)."""

    def _run(self, elapsed_iter2):
        import unittest.mock as um
        h1, h2 = '1' * 40, '2' * 40
        times = iter([1000.0, 1001.0, 1000.0 + elapsed_iter2])
        dht_calls = []
        old = dict(TH._SEED)
        try:
            TH._SEED.clear()
            with um.patch.object(TH, '_scrape_hashes', return_value={h1: 0, h2: 0}), \
                    um.patch.object(TH, '_announce_hints', return_value=None), \
                    um.patch.object(TH, '_dht_peers',
                                    side_effect=lambda h: dht_calls.append(h) or 0), \
                    um.patch.object(TH, '_seed_save'), \
                    um.patch.object(TH.time, 'time', side_effect=lambda: next(times)):
                TH._scrape_submit([(h1, []), (h2, [])])
        finally:
            TH._SEED.clear()
            TH._SEED.update(old)
        return dht_calls

    def test_budget_90_corta(self):
        """elapsed=91 > 90 -> break: solo 1 _dht_peers (mutante 90+1@367:
        >91 dejaría pasar la 2ª)."""
        self.assertEqual(len(self._run(91.0)), 1)

    def test_budget_exacto_90_no_corta(self):
        """elapsed=90 NO es >90 -> sigue: 2 _dht_peers (mutante Gt@367:
        >=90 cortaría)."""
        self.assertEqual(len(self._run(90.0)), 2)
