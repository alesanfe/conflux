"""Sesiones HTTP por host + fetch con reintentos/timeout."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import json
import time
import urllib.parse

import requests

from conflux.core.state import _SESS, HOST_CD, HOST_OPEN, UA, _fails, _sess_lock


def _session(host):
    s = _SESS.get(host)
    if s is None:
        with _sess_lock:
            if len(_SESS) >= 64:                            # hosts de streams/CDN son
                _SESS.clear()                               # ilimitados: recrear es barato
            s = _SESS.setdefault(host, requests.Session())
            s.headers.update(UA)
            s.headers['Accept-Encoding'] = 'gzip'
    return s


def _strike(host):
    """Strike del disyuntor por host + poda de entradas ya caducadas
    (st[1] viejo = cooldown pasado: el registro es peso muerto)."""
    now = time.time()
    _fails[host] = (_fails.get(host, (0, 0))[0] + 1, now)
    if len(_fails) > 200:                                   # acotado: hosts muertos
        for h, (_n, ts) in list(_fails.items()):            # aleatorios no se acumulan
            if now - ts > HOST_CD:
                _fails.pop(h, None)


def fetch(url, timeout=7):
    """GET JSON con sesión por-host, reintentos y disyuntor por host."""
    host = urllib.parse.urlsplit(url).netloc
    st = _fails.get(host)
    if st and st[0] >= HOST_OPEN and time.time() - st[1] < HOST_CD:
        return None                                          # disyuntor abierto: host muerto = fail-fast
    for att in range(2):                                        # 1 reintento con backoff: flakes transitorios
        try:
            r = _session(host).get(url, timeout=timeout, stream=True)
            try:
                body = r.raw.read(6_000_001, decode_content=True)   # gzip transparente
                if len(body) > 6_000_000:
                    return None           # respuesta gigante: descartar
                _fails.pop(host, None)                           # éxito: strikes a cero
                return json.loads(body)
            finally:
                r.close()
        except Exception:                                    # noqa: BLE001 — cualquier fallo de red/parseo = strike del host
            _strike(host)
            if not att:
                time.sleep(0.3)
    return None
