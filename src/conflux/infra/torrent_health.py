"""Salud de torrents: scrape UDP a trackers públicos -> veredicto por infoHash.

Regla (verificada en vivo contra el streaming server de Stremio :11470):
- seeds > 0 en tracker público          -> swarm vivo: usable
- seeds == 0 pero el stream lleva hints (`sources` tracker:/dht:)
                                        -> swarm privado posible: se mantiene
- seeds == 0 Y sin hints                -> inalcanzable (Orion-style): fuera
- scrape sin respuesta (trackers caídos)-> desconocido: fail-open, se mantiene
Los veredictos se cachean _TTL: un collect posterior no repite UDP."""
# pylint: disable=broad-exception-caught
# broad-exception: UDP es inherently flaky; cualquier fallo = tracker no usable
import random
import socket
import struct
import threading
import time

from conflux.core.cfg import _cfgget
from conflux.core.state import _POOL_BG

_TRACKERS = [('open.demonii.com', 1337), ('tracker.torrent.eu.org', 451),
             ('open.stealth.si', 80), ('tracker.dler.org', 6969)]

_CHUNK = 20              # tandas grandes las dropean los trackers
_TTL = 6 * 3600          # veredicto 6h: swarms reviven/mueren, no es eterno
_TMO = 4                 # s por intento de tracker

_SEED: dict = {}         # infohash_lower -> (seeds, ts) — solo si el tracker respondió
_SLOCK = threading.Lock()


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


def _scrape_submit(hashes):
    """Worker: scrapea y guarda veredictos (los no respondidos quedan unknown)."""
    res = _scrape_hashes(hashes)
    now = time.time()
    with _SLOCK:
        if len(_SEED) > 4000:                       # cota: purga la mitad más vieja
            for h in sorted(_SEED, key=lambda h: _SEED[h][1])[:len(_SEED) // 2]:
                _SEED.pop(h, None)
        for h, sd in res.items():
            _SEED[h] = (sd, now)


def _hash_known(h):
    """Veredicto fresco del hash -> seeds | None (desconocido o caducado)."""
    v = _SEED.get(h)
    if v is None or time.time() - v[1] > _TTL:
        return None
    return v[0]


def _hash_dead(s):
    """El stream es inalcanzable: scrape dijo 0 seeds y no hay hints sources."""
    h = s.get('infoHash')
    if not h or s.get('sources'):
        return False                                    # hints = swarm privado posible
    return _hash_known(str(h).lower()) == 0


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
            todo.append(h)
    if dead:
        streams[:] = [s for s in streams
                      if str(s.get('infoHash') or '').lower() not in dead]
    for i in range(0, min(len(todo), 140), _CHUNK * 7):   # ~140 hashes por lote
        _POOL_BG.submit(_scrape_submit, todo[i:i + _CHUNK * 7])
