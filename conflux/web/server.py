"""Servidor HTTP: handler, ROUTES, refresher/prefetch, main()."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import gzip
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conflux.app.bg import _prefetch_catalogs, _refresher
from conflux.app.meta import handle_meta
from conflux.app.stream import handle_stream
from conflux.app.subs import _sub_download, handle_subtitles
from conflux.core.cfg import _PROF, _profile_load, load_config
from conflux.core.state import (
    CFG,
    PORT,
    SOURCES,
    _cache,
    _cat_c,
    _meta_c,
    _rtime,
    _rts,
    log_ev,
    manifests,
)
from conflux.domain.catalog import handle_catalog
from conflux.domain.sources import _load_manifests_cache, refresh_manifests
from conflux.infra.debrid import _db_resolve
from conflux.web.pages import (
    _admin_page,
    _configure_page,
    _configure_save,
    _health_page,
    _manifest_reply,
    _trakt_auth_page,
    _trakt_status,
)


class H(BaseHTTPRequestHandler):
    """Handler HTTP: router Stremio + páginas /configure /admin /health."""
    # do_GET/do_POST/do_OPTIONS son nombres impuestos por BaseHTTPRequestHandler
    # pylint: disable=invalid-name
    protocol_version = 'HTTP/1.1'   # keep-alive: menos TCP por petición
    timeout = 30                    # cliente que conecta y calla no retiene el hilo
    def log_message(self, format, *args):
        ua = self.headers.get('User-Agent', '-') if hasattr(self, 'headers') else '-'
        log_ev('req', msg=format % args, ua=ua[:60])
    def reply(self, obj, code=200):
        """JSON (+gzip si aplica) con keep-alive HTTP/1.1."""
        body = json.dumps(obj).encode()
        gz = len(body) > 1024 and 'gzip' in self.headers.get('Accept-Encoding', '')
        if gz:
            body = gzip.compress(body)
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        if gz:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                              # Stremio cortó la espera — sin ruido
    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Content-Length', '0')
        self.end_headers()
    def _profile(self, p):
        """/p/<nombre>/... -> carga su config y devuelve el path real."""
        pm = re.match(r'^/p/([a-zA-Z0-9_-]{1,24})(/.*)$', p)           # perfil: /p/<nombre>/…
        if pm:
            _PROF.name, _PROF.cfg = pm.group(1), _profile_load(pm.group(1))
            return pm.group(2)
        _PROF.name, _PROF.cfg = None, None
        return p
    def _timed_route(self, p):
        t0 = time.time()
        seg = p.split('/')[1] if '/' in p else 'root'
        try:
            for rx, h in ROUTES:
                m = rx.match(p)
                if m:
                    return self.reply(h(m))
            return self.reply({'err': 'not found'}, 404)
        finally:
            ms = (time.time() - t0) * 1000
            n, tot, mx = _rtime.get(seg, (0, 0, 0))
            _rtime[seg] = (n + 1, tot + ms, max(mx, ms))   # latencia por ruta en /health
            _rts.setdefault(seg, deque(maxlen=512)).append(ms)
    # dispatcher HTTP: los muchos returns son la forma natural del router
    # pylint: disable=too-many-return-statements
    def do_GET(self):
        """Router GET: stream/catalog/meta/subtitles + páginas y redirects."""
        p = self._profile(urllib.parse.unquote(self.path.split('?', 1)[0]))  # sin query string
        if p == '/manifest.json':
            return self.reply(_manifest_reply())
        if p == '/health':
            return self.reply(_health_page())
        if p == '/admin':
            return self._html(_admin_page())
        if p == '/configure':
            return self._html(_configure_page())
        if p == '/auth/trakt':
            return self._html(_trakt_auth_page())
        if p == '/auth/trakt/status':
            return self.reply(_trakt_status())
        if p.startswith('/r/'):                                        # debrid redirect
            _, _, pvd, h = p.split('/')
            fi = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('fi', ['0'])[0]
            return self._redir(_db_resolve(pvd, h, fi))
        if p.startswith('/s/'):                                        # subtitle redirect
            return self._redir(_sub_download(p.rsplit('/', 1)[-1]))
        return self._timed_route(p)
    def _redir(self, u, code=302):
        self.send_response(code if u else 404)
        if u:
            self.send_header('Location', u)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _html(self, body):
        b = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_POST(self):
        """Router POST: /configure, /reload, /auth."""
        p = self.path.split('?', 1)[0]
        if p == '/reload':
            load_config()
            _cache.clear()
            _meta_c.clear()
            _cat_c.clear()
            threading.Thread(target=refresh_manifests, daemon=True).start()
            log_ev('reload', cfg=CFG.copy())
            return self.reply({'ok': True, 'config': {k: ('***' if 'key' in k else v)
                                                      for k, v in CFG.items()}})
        if p == '/configure':
            ln = int(self.headers.get('Content-Length') or 0)
            form = urllib.parse.parse_qs(self.rfile.read(min(ln, 65536)).decode())
            _configure_save(form)
            return self._redir('/configure', code=303)
        return self.reply({'err': 'not found'}, 404)





ROUTES = [
    (re.compile(r'^/subtitles/([^/]+)/(.+)\.json$'),
     lambda m: handle_subtitles(m.group(1), m.group(2))),
    (re.compile(r'^/stream/([^/]+)/(.+)\.json$'),
     lambda m: handle_stream(m.group(1), m.group(2))),
    (re.compile(r'^/catalog/([^/]+)/([^/]+)/(.+)\.json$'),
     lambda m: handle_catalog(m.group(1), urllib.parse.unquote(m.group(2)), m.group(3))),
    (re.compile(r'^/catalog/([^/]+)/([^/]+)\.json$'),
     lambda m: handle_catalog(m.group(1), urllib.parse.unquote(m.group(2)), None)),
    (re.compile(r'^/meta/([^/]+)/(.+)\.json$'),
     lambda m: handle_meta(m.group(1), m.group(2))),
]

def main():
    """Arranque: manifests cacheados si hay -> refresh bg; si no, fetch en vivo."""
    cached = _load_manifests_cache()
    if cached:
        manifests[:] = cached
        threading.Thread(target=refresh_manifests, daemon=True).start()   # fresco en bg
        log_ev('manifests', n=sum(1 for m in manifests if m), total=len(SOURCES), src='cache')
    else:
        refresh_manifests()
        log_ev('manifests', n=sum(1 for m in manifests if m), total=len(SOURCES), src='live')
    threading.Thread(target=_refresher, daemon=True).start()
    threading.Thread(target=_prefetch_catalogs, daemon=True).start()   # caché caliente antes del 1er cliente

    class Srv(ThreadingHTTPServer):
        """Servidor con hilos daemon (sin zombies al parar)."""
        daemon_threads = True   # sin hilos zombie
    Srv(('127.0.0.1', PORT), H).serve_forever()
