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
            TH._scrape_submit([H_DEAD])
        self.assertEqual(TH._SEED[H_DEAD][0], -1)

    def test_cero_tracker_dht_vivo(self):
        import unittest.mock as um
        with um.patch.object(TH, '_scrape_hashes', return_value={H_DEAD: 0}), \
                um.patch.object(TH, '_dht_peers', return_value=7):
            TH._scrape_submit([H_DEAD])
        self.assertEqual(TH._SEED[H_DEAD][0], 99)   # vivo via DHT

    def test_seeds_directo_sin_dht(self):
        import unittest.mock as um
        with um.patch.object(TH, '_scrape_hashes', return_value={H_LIVE: 5}), \
                um.patch.object(TH, '_dht_peers') as dp:
            TH._scrape_submit([H_LIVE])
        self.assertEqual(TH._SEED[H_LIVE][0], 5)
        dp.assert_not_called()                       # seeds>0: DHT no hace falta


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
