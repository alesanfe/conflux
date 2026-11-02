"""Sesiones HTTP por host + fetch con reintentos/timeout."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import json
import time
import urllib.parse
import urllib.request

import requests

from conflux.core.state import _SESS, UA, _fails, _sess_lock


def _session(host):
    s = _SESS.get(host)
    if s is None:
        with _sess_lock:
            s = _SESS.setdefault(host, requests.Session())
            s.headers.update(UA)
            s.headers['Accept-Encoding'] = 'gzip'
    return s


def fetch(url, timeout=7):
    """GET JSON con sesión por-host, reintentos y _health/log_ev implícitos."""
    host = urllib.parse.urlsplit(url).netloc
    for att in range(2):                                        # 1 reintento con backoff: flakes transitorios
        try:
            r = _session(host).get(url, timeout=timeout, stream=True)
            try:
                body = r.raw.read(6_000_001, decode_content=True)   # gzip transparente
                if len(body) > 6_000_000:
                    return None           # respuesta gigante: descartar
                return json.loads(body)
            finally:
                r.close()
        except Exception:                                    # noqa: BLE001 — cualquier fallo de red/parseo = strike del host
            _fails[host] = _fails.get(host, 0) + 1
            if not att:
                time.sleep(0.3)
    return None
