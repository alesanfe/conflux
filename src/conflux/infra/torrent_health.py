"""Salud de torrents: scrape UDP a trackers públicos -> veredicto por infoHash.

Regla (verificada en vivo contra el streaming server de Stremio :11470):
- seeds > 0 en tracker público          -> swarm vivo: usable
- seeds == 0 y el stream lleva hints tracker: -> ANNOUNCE a sus propios
  trackers: swarm vivo -> 99; >=2 responden sin swarm -> -2 (el cliente
  tampoco podría unirse: fuera aunque tenga hints); todos timeout -> DHT
- seeds == 0 sin hints -> DHT get_peers decide: peers>0 vivo (99), 0 -> -1
- veredicto -1 y sin hints              -> inalcanzable (Orion-style): fuera
- veredicto -2 (announce-dead)          -> fuera, con o sin hints
- scrape/announce/DHT sin respuesta     -> desconocido: fail-open, se mantiene
Los veredictos se cachean _TTL: un collect posterior no repite UDP.
DHT es best-effort: si la red no lo permite (NAT estricto/routers muertos)
devuelve 0 como "0 confirmado" — igual resultado que sin la capa DHT."""
# pylint: disable=broad-exception-caught
# broad-exception: UDP es inherently flaky; cualquier fallo = tracker no usable
import os
import random
import re
import socket
import struct
import threading
import time

from conflux.core.cfg import _cfgget
from conflux.core.state import _POOL_BG, _ROOT

_SEED_PATH = os.path.join(_ROOT, '.seed-cache.json')

_TRACKERS = [('open.demonii.com', 1337), ('tracker.torrent.eu.org', 451),
             ('open.stealth.si', 80), ('tracker.dler.org', 6969)]

_CHUNK = 20              # tandas grandes las dropean los trackers
_TTL = 6 * 3600          # veredicto 6h: swarms reviven/mueren, no es eterno
_TMO = 4                 # s por intento de tracker

_SEED: dict = {}         # infohash_lower -> (seeds, ts) — -1 = muerto confirmado (0 tracker + 0 DHT)
_SLOCK = threading.Lock()
_LAST_SAVE = [0.0]       # throttle de persistencia


def _seed_load(path):
    """Carga veredictos persistidos (no caducados) -> evita re-scrape frío."""
    try:
        import json
        import os
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        now = time.time()
        for h, (sd, ts) in data.items():
            if isinstance(sd, int) and now - ts < _TTL:
                _SEED[h] = (sd, ts)
    except Exception:
        pass


def _seed_save(path):
    """Persiste veredictos (tmp+rename, throttle 60s)."""
    now = time.time()
    if now - _LAST_SAVE[0] < 60:
        return
    _LAST_SAVE[0] = now
    try:
        import json
        import os
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(_SEED, f)
        os.replace(tmp, path)
    except Exception:
        pass


# ---- bencode mínimo (KRPC DHT) ----

def _bdec(b, i=0):
    """bdecode lo justo para respuestas KRPC -> (objeto, offset)."""
    t = b[i:i + 1]
    if t == b'i':
        j = b.index(b'e', i)
        return int(b[i + 1:j]), j + 1
    if t == b'l':
        i += 1
        out = []
        while b[i:i + 1] != b'e':
            v, i = _bdec(b, i)
            out.append(v)
        return out, i + 1
    if t == b'd':
        i += 1
        dic = {}
        while b[i:i + 1] != b'e':
            k, i = _bdec(b, i)
            v, i = _bdec(b, i)
            dic[k] = v
        return dic, i + 1
    j = b.index(b':', i)
    n = int(b[i:j])
    return b[j + 1:j + 1 + n], j + 1 + n


def _benc(x):
    if isinstance(x, int):
        return b'i' + str(x).encode() + b'e'
    if isinstance(x, (bytes, bytearray)):
        return str(len(x)).encode() + b':' + bytes(x)
    if isinstance(x, str):
        x = x.encode()
        return str(len(x)).encode() + b':' + x
    if isinstance(x, dict):
        return b'd' + b''.join(_benc(k) + _benc(x[k]) for k in sorted(x)) + b'e'
    if isinstance(x, (list, tuple)):
        return b'l' + b''.join(_benc(v) for v in x) + b'e'
    raise TypeError(type(x))


# ---- DHT get_peers: swarm vivo sin trackers ----

_NID = random.randbytes(20) if hasattr(random, 'randbytes') else \
    bytes(random.getrandbits(8) for _ in range(20))
_DHT_BOOT = [('router.bittorrent.com', 6881), ('router.utorrent.com', 6881),
             ('dht.transmissionbt.com', 6881)]
_DHT_NODES: list[tuple[str, int]] = []   # (ip, port) — aprendidos via find_node; LRU 64


def _krpc(s, addr, q, args, timeout=3):
    """Un query KRPC -> dict r | None."""
    tid = random.randbytes(2) if hasattr(random, 'randbytes') else \
        bytes([random.getrandbits(8), random.getrandbits(8)])
    try:
        s.sendto(_benc({b't': tid, b'y': b'q', b'q': q.encode(), b'a': args}), addr)
        r, _ = s.recvfrom(65535)
        d, _ = _bdec(r)
        if d.get(b't') == tid and d.get(b'y') in (b'r', b'e'):
            return d.get(b'r') or {}
    except Exception:
        pass
    return None


def _dht_nodes(s, timeout=3):
    """Semilla de nodos DHT: find_node sobre los bootstrap -> [(ip,port)]."""
    global _DHT_NODES
    if _DHT_NODES:
        return list(_DHT_NODES)
    nodes = []
    for host, port in _DHT_BOOT:
        try:
            r = _krpc(s, (socket.gethostbyname(host), port), 'find_node',
                      {b'id': _NID, b'target': _NID}, timeout)
        except Exception:
            continue
        nb = (r or {}).get(b'nodes') or b''
        nodes += [(socket.inet_ntoa(nb[i + 20:i + 24]),
                   struct.unpack('!H', nb[i + 24:i + 26])[0])
                  for i in range(0, len(nb) - 25, 26)]
    _DHT_NODES = nodes[:64]
    return list(_DHT_NODES)


def _dht_peers(infohash, cap=10, timeout=3):
    """get_peers iterando nodos cercanos -> nº de peers encontrados (0 = no visto).
    Private-flag nunca aparece en DHT — eso es esperado, no un fallo."""
    h = bytes.fromhex(infohash)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
    except Exception:
        return 0
    try:
        todo = _dht_nodes(s)
        seen, asked = set(), 0
        peers = 0
        while todo and asked < cap and not peers:
            ip, port = todo.pop(0)
            if (ip, port) in seen:
                continue
            seen.add((ip, port))
            asked += 1
            r = _krpc(s, (ip, port), 'get_peers',
                      {b'id': _NID, b'info_hash': h}, timeout)
            if not r:
                continue
            peers += len(r.get(b'values') or [])      # cada value = un peer 6B
            nb = r.get(b'nodes') or b''
            for i in range(0, len(nb) - 25, 26):
                a = (socket.inet_ntoa(nb[i + 20:i + 24]),
                     struct.unpack('!H', nb[i + 24:i + 26])[0])
                if a not in seen:
                    todo.append(a)
        return peers
    finally:
        try:
            s.close()
        except Exception:
            pass


def _scrape_tracker(host, port, hashes, timeout=_TMO):
    """Un scrape UDP -> {h: seeds}; {} si el tracker no responde."""
    s = None
    try:
        ip = socket.gethostbyname(host)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        tx = random.randint(0, 2**31)
        s.sendto(struct.pack('!QII', 0x41727101980, 0, tx), (ip, port))
        r0, _ = s.recvfrom(2048)
        if len(r0) < 16:
            return {}
        _, rtx, cid = struct.unpack('!IIQ', r0[:16])
        if rtx != tx:
            return {}
        body = b''.join(bytes.fromhex(h) for h in hashes)
        tx2 = random.randint(0, 2**31)
        s.sendto(struct.pack('!QII', cid, 2, tx2) + body, (ip, port))
        r, _ = s.recvfrom(65535)
        _, rtx2 = struct.unpack('!II', r[:8])
        if rtx2 != tx2 or len(r) < 8:
            return {}
        return {h: struct.unpack('!III', r[o:o + 12])[0]
                for h, o in zip(hashes, range(8, len(r) - 11, 12))}
    except Exception:
        return {}
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


# ---- announce a trackers de los hints `sources` (veredicto fuerte) ----
# Si el swarm se anuncia en ESE tracker (aunque mis 4 públicos digan 0), el
# torrent vive; si el tracker responde con 0 seeds/peers o error, y ninguno
# da swarm, el cliente tampoco puede unirse -> muerto de verdad (-2).

_PID = b'-CF0001-' + random.randbytes(12) if hasattr(random, 'randbytes') else \
    b'-CF0001-' + bytes(random.getrandbits(8) for _ in range(12))


def _announce_udp(host, port, infohash, timeout=4):
    """UDP announce -> {'seeds','peers'} | 'deny' | None. Solo handshake."""
    s = None
    try:
        ip = socket.gethostbyname(host)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        tx = random.randint(0, 2**31)
        s.sendto(struct.pack('!QII', 0x41727101980, 0, tx), (ip, port))
        r0, _ = s.recvfrom(2048)
        if len(r0) < 16:
            return None
        act, rtx, cid = struct.unpack('!IIQ', r0[:16])
        if act == 3 or rtx != tx:
            return 'deny' if act == 3 else None
        tx2 = random.randint(0, 2**31)
        pkt = struct.pack('!QII', cid, 1, tx2) + bytes.fromhex(infohash) + _PID + \
            struct.pack('!QQQIIIiH', 0, 1, 0, 0, 0,
                        random.randint(0, 2**31), -1, 6881)
        s.sendto(pkt, (ip, port))
        r, _ = s.recvfrom(65535)
        if len(r) < 20:
            return None
        act, rtx2 = struct.unpack('!II', r[:8])
        if act == 3 or rtx2 != tx2:
            return 'deny' if act == 3 else None
        _iv, leech, seeds = struct.unpack('!III', r[8:20])
        return {'seeds': seeds, 'leechers': leech,
                'peers': max(0, (len(r) - 20) // 6)}
    except Exception:
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def _announce_http(url, infohash, timeout=5):
    """HTTP(S) announce -> {'seeds','peers'} | 'deny' | None."""
    import urllib.parse
    import urllib.request
    try:
        q = ('info_hash=' + urllib.parse.quote_from_bytes(bytes.fromhex(infohash)) +
             '&peer_id=' + urllib.parse.quote_from_bytes(_PID) +
             '&port=6881&uploaded=0&downloaded=0&left=1&compact=1&event=started')
        sep = '&' if '?' in url else '?'
        req = urllib.request.Request(url + sep + q, headers={
            'User-Agent': 'qBittorrent/4.6.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 (caller restringe a http/https)
            d, _ = _bdec(r.read(65536))
        if not isinstance(d, dict):
            return None
        if d.get(b'failure reason'):
            return 'deny'
        peers = d.get(b'peers') or b''
        return {'seeds': d.get(b'complete') or 0,
                'leechers': d.get(b'incomplete') or 0,
                'peers': (len(peers) // 6) if isinstance(peers, (bytes, bytearray))
                         else len(peers) if isinstance(peers, list) else 0}
    except Exception:
        return None


def _announce_hints(infohash, urls, cap=6):
    """Anuncia a los trackers hinteados (máx cap): 'alive' | 'dead' | 'unknown'.
    dead = >=2 trackers respondieron y ninguno ve swarm (el cliente tampoco
    podría unirse); unknown = todos timeout (tracker caído != torrent muerto)."""
    answered, tried = 0, 0
    for u in urls[:cap]:
        tried += 1
        m = re.match(r'(udp|https?)://([^/?]+)', u)
        if not m:
            continue
        if m.group(1) == 'udp':
            host, _, port = m.group(2).partition(':')
            r = _announce_udp(host, int(port or 80), infohash)
        else:
            r = _announce_http(u, infohash)
        if r is None:
            continue
        answered += 1
        if r == 'deny':
            continue
        if r.get('seeds') or r.get('peers') or r.get('leechers'):
            return 'alive'
    return 'dead' if answered >= 2 else 'unknown'


def _scrape_hashes(hashes):
    """Chunk a chunk, trackers en orden -> {h: seeds} (ausente = desconocido)."""
    out = {}
    for i in range(0, len(hashes), _CHUNK):
        chunk = hashes[i:i + _CHUNK]
        got = {}
        for host, port in _TRACKERS:
            got = _scrape_tracker(host, port, chunk)
            if got:
                break
        out.update(got)
    return out


def _scrape_submit(items):
    """Worker: scrapea trackers públicos; los 0-seed con hints se ANUNCIAN a
    sus propios trackers (veredicto fuerte: si ninguno ve swarm, el cliente
    tampoco puede unirse -> -2); sin hints -> DHT. >0/99 vivo, -1/-2 muerto."""
    hashes = [h for h, _ in items]
    hints = dict(items)
    res = _scrape_hashes(hashes)
    now = t0 = time.time()
    verdicts = {h: sd for h, sd in res.items() if sd > 0}
    zero = [h for h in hashes if res.get(h, 0) == 0]     # 0 o ausente del scrape
    hinted = [(h, hints[h]) for h in zero if hints.get(h)]
    ann = {}
    if hinted:                                       # announces en paralelo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as ex:            # pool local, no roba _POOL_BG
            ann = dict(zip((h for h, _ in hinted),
                           ex.map(lambda p: _announce_hints(p[0], p[1]), hinted)))
    for h in zero:
        if time.time() - t0 > 90:                    # budget: DHT es secuencial
            break                                    # el resto queda unknown -> retry
        st = ann.get(h)
        if st == 'alive':
            verdicts[h] = 99
        elif st == 'dead':
            verdicts[h] = -2                           # sus trackers no lo ven
        else:                                        # sin hints o announce-unknown
            verdicts[h] = -1 if _dht_peers(h) == 0 else 99
    with _SLOCK:
        if len(_SEED) > 4000:                       # cota: purga la mitad más vieja
            for h in sorted(_SEED, key=lambda h: _SEED[h][1])[:len(_SEED) // 2]:
                _SEED.pop(h, None)
        for h, sd in verdicts.items():
            _SEED[h] = (sd, now)
    _seed_save(_SEED_PATH)                           # sobrevive reinicios


def _hash_known(h):
    """Veredicto fresco del hash -> seeds | None (desconocido o caducado)."""
    v = _SEED.get(h)
    if v is None or time.time() - v[1] > _TTL:
        return None
    return v[0]


def _hash_dead(s):
    """Inalcanzable confirmado: -2 (sus propios trackers no ven swarm — el
    cliente tampoco podría unirse) o -1 sin hints (ni trackers ni DHT)."""
    h = s.get('infoHash')
    if not h:
        return False
    v = _hash_known(str(h).lower())
    if v == -2:
        return True                                     # announce-dead, con o sin hints
    return v == -1 and not s.get('sources')


def _hash_health(streams):
    """Saca los infoHash confirmadamente inalcanzables; encola scrape async de
    los desconocidos (máx ~140/request — el resto espera a la próxima)."""
    if not streams or not _cfgget('torrent_health', True):
        return
    dead, todo, seen = set(), [], set()
    for s in streams:
        h = str(s.get('infoHash') or '').lower()
        if not h or len(h) != 40:
            continue
        if _hash_dead(s):
            dead.add(h)
        elif _hash_known(h) is None and h not in seen:
            seen.add(h)
            urls = [u[8:] for u in (s.get('sources') or [])
                    if isinstance(u, str) and u.startswith('tracker:')]
            todo.append((h, urls))
    if dead:
        streams[:] = [s for s in streams
                      if str(s.get('infoHash') or '').lower() not in dead]
    sink = [i for i, s in enumerate(streams)
            if s.get('sources')
            and _hash_known(str(s.get('infoHash') or '').lower()) in (-1, -2)]
    if 0 < len(sink) < len(streams):               # muertos-con-hints al fondo:
        sset = set(sink)                           # sort estable: orden relativo intacto
        streams[:] = [s for i, s in enumerate(streams) if i not in sset] + \
                     [streams[i] for i in sink]
    for i in range(0, min(len(todo), 140), _CHUNK * 7):   # ~140 hashes por lote
        _POOL_BG.submit(_scrape_submit, todo[i:i + _CHUNK * 7])


_seed_load(_SEED_PATH)                             # veredictos persistidos al import
