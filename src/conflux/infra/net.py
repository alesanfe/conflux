"""Sesiones HTTP por host + fetch con reintentos/timeout."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import ipaddress
import json
import socket
import time
import urllib.parse

import requests

from conflux.core.state import CLOCK, _SESS, HOST_CD, HOST_OPEN, UA, _fails, _sess_lock


def _session(host):
    s = _SESS.get(host)
    if s is None:
        with _sess_lock:
            if len(_SESS) >= 64:                            # hosts de streams/CDN son
                _SESS.clear()                               # ilimitados: recrear es barato
            s = _SESS.setdefault(host, requests.Session())
            # pool_maxsize 32: ráfagas de probes (32 posters, 40 _dead_videos)
            # al mismo host no descartan conexiones (handshake TLS por hit).
            ad = requests.adapters.HTTPAdapter(pool_maxsize=32)
            s.mount('https://', ad)
            s.mount('http://', ad)
            s.headers.update(UA)
            s.headers['Accept-Encoding'] = 'gzip'
    return s


def _strike(host):
    """Strike del disyuntor por host + poda de entradas ya caducadas
    (st[1] viejo = cooldown pasado: el registro es peso muerto)."""
    now = CLOCK.time()
    _fails[host] = (_fails.get(host, (0, 0))[0] + 1, now)
    if len(_fails) > 200:                                   # acotado: hosts muertos
        for h, (_n, ts) in list(_fails.items()):            # aleatorios no se acumulan
            if now - ts > HOST_CD:
                _fails.pop(h, None)


_PUBLIC_URL_OK: dict = {}                    # url -> bool (memo; DNS por request sería caro)


def _public_url_cached(url):
    """_public_url memoizada — los fetchers la consultan por request."""
    ok = _PUBLIC_URL_OK.get(url)
    if ok is None:
        if len(_PUBLIC_URL_OK) > 512:
            _PUBLIC_URL_OK.clear()
        ok = _public_url(url)
        _PUBLIC_URL_OK[url] = ok
    return ok


def _public_url(url):
    """Guard SSRF para URLs remotas configurables (epg_url, M3U_URL):
    scheme http/https y el host no resuelve a link-local (169.254.169.254 =
    metadata cloud), multicast, reservado ni unspecified. RFC1918/loopback SE
    permiten — Prowlarr/NAS/xmltv en LAN son uso legitimo en un proxy local."""
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname:
            return False
        port = p.port or (443 if p.scheme == 'https' else 80)
        infos = socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP)
        if not infos:
            return False
        for _fam, _t, _pr, _cn, sa in infos:
            ip = ipaddress.ip_address(sa[0])
            if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return False
        return True
    except (OSError, ValueError):
        return False


def fetch(url, timeout=7):
    """GET JSON con sesión por-host, reintentos y disyuntor por host."""
    host = urllib.parse.urlsplit(url).netloc
    st = _fails.get(host)
    if st and st[0] >= HOST_OPEN and CLOCK.time() - st[1] < HOST_CD:
        return None                                          # disyuntor abierto: host muerto = fail-fast
    for att in range(2):                                        # 1 reintento con backoff: flakes transitorios
        try:
            r = _session(host).get(url, timeout=timeout, stream=True)
            try:
                body = r.raw.read(6_000_001, decode_content=True)   # gzip transparente
                if len(body) > 6_000_000:
                    return None           # respuesta gigante: descartar
                _fails.pop(host, None)                           # éxito: strikes a cero
                d = json.loads(body)
                return d if isinstance(d, (dict, list)) else None  # contrato: doc/compuesto
            finally:
                r.close()
        except Exception:                                    # noqa: BLE001 — cualquier fallo de red/parseo = strike del host
            _strike(host)
            if not att:
                time.sleep(0.3)
    return None
