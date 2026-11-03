"""Servidor HTTP: handler, ROUTES, refresher/prefetch, main()."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import base64
import binascii
import gzip
import hmac
import json
import re
import signal
import ssl
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conflux.app.bg import _prefetch_catalogs, _refresher
from conflux.app.meta import handle_meta
from conflux.app.stream import handle_stream
from conflux.app.subs import _sub_download, handle_subtitles
from conflux.core.cfg import _PROF, _profile_load, load_config
from conflux.core.state import (
    _MF_CACHE,
    CFG,
    PORT,
    SOURCES,
    _atomic_json,
    _cache,
    _cat_c,
    _meta_c,
    _rtime,
    _rts,
    log_ev,
    manifests,
)
from conflux.domain.catalog import handle_catalog
from conflux.domain.sources import _load_manifests_cache, _persist_manifests, refresh_manifests
from conflux.infra.debrid import _db_resolve
from conflux.web.pages import (
    _admin_page,
    _configure_page,
    _configure_save,
    _health_page,
    _landing_page,
    _login_page,
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
        self.send_header('X-Content-Type-Options', 'nosniff')
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
    def _authd(self):
        """config.admin_token vacío -> abierto (loopback); si no, exige header
        X-Admin-Token (API) o cookie conflux_auth (navegador). El token NUNCA va
        en la URL: ?token= se filtraba a historial/Referer/logs — el login pone
        cookie HttpOnly+SameSite=Strict y sale del historial."""
        tok = CFG.get('admin_token')
        if not tok:
            return True
        if hmac.compare_digest(tok, self.headers.get('X-Admin-Token') or ''):
            return True                                        # timing-safe
        for c in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = c.strip().partition('=')
            if k == 'conflux_auth' and hmac.compare_digest(tok, v):
                return True
        return False
    # claves permitidas en la cfg de la URL (nada de paths/URLs -> SSRF;
    # nada de trakt_*/admin/tls -> tokens del servidor fuera de scope)
    _CFG_URL_KEYS = frozenset({
        'stream_langs', 'stream_exclude', 'rpdb_key',
        'catalogs_disabled', 'sources_disabled', 'source_order',
        'debrid_provider', 'debrid_key', 'calendar_months_ahead',
        'stream_max_gb', 'stream_prefetch',
        'opensubtitles_key', 'opensubtitles_user', 'opensubtitles_pass', 'mdblist_key'})
    _CFG_URL_LIST = frozenset({'stream_langs', 'stream_exclude', 'catalogs_disabled',
                               'sources_disabled', 'source_order'})
    _CFG_URL_NUM = {'calendar_months_ahead': (0, 24),      # cota: builds de calendario
                    'stream_max_gb': (0, 100000)}
    _CFG_URL_BOOL = frozenset({'stream_prefetch'})

    @classmethod
    def _cfg_url_ok(cls, k, v):
        """Whitelist + tipos: un list-clave como int/str rompe los patrones
        (itera chars/int); un int-clave como str contamina config."""
        if k not in cls._CFG_URL_KEYS:
            return False
        if k in cls._CFG_URL_LIST:
            return isinstance(v, list) and all(isinstance(x, str) for x in v)
        if k in cls._CFG_URL_NUM:
            lo, hi = cls._CFG_URL_NUM[k]
            return type(v) is int and lo <= v <= hi
        if k in cls._CFG_URL_BOOL:
            return type(v) is bool
        return isinstance(v, str)

    def _profile(self, p):
        """/p/<nombre>/... o /cfg/<b64json>/... -> carga config y devuelve el path real."""
        pm = re.match(r'^/p/([a-zA-Z0-9_-]{1,24})(/.*)$', p)           # perfil: /p/<nombre>/…
        if pm:
            _PROF.name, _PROF.cfg = pm.group(1), _profile_load(pm.group(1))
            return pm.group(2)
        cm = re.match(r'^/cfg/([A-Za-z0-9_=-]{8,4096})(/.*)$', p)      # cfg en URL (estilo Torrentio)
        if cm:
            try:
                seg = cm.group(1)
                dc = json.loads(base64.urlsafe_b64decode(seg + '=' * (-len(seg) % 4)))
                if isinstance(dc, dict):
                    _PROF.name = 'cfg-' + seg[:8]
                    _PROF.cfg = {k: v for k, v in dc.items() if self._cfg_url_ok(k, v)}
                    return cm.group(2)
            except (ValueError, binascii.Error):
                pass                                                # cfg rota -> perfil normal
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
            if seg not in _rtime and len(_rtime) >= 64:     # paths arbitrarios de
                seg = '_other'                              # crawlers: no clave nueva
            n, tot, mx = _rtime.get(seg, (0, 0, 0))
            _rtime[seg] = (n + 1, tot + ms, max(mx, ms))   # latencia por ruta en /health
            _rts.setdefault(seg, deque(maxlen=512)).append(ms)
    # dispatcher de rutas fijas: los returns tempranos son su forma natural
    # pylint: disable=too-many-return-statements
    def _pages(self, p):
        """Rutas fijas GET (manifest/health/páginas). True si las sirvió."""
        if p in ('/admin', '/configure') and not self._authd():
            self._html(_login_page(p), 401)                     # form login -> cookie HttpOnly
            return True
        if p in ('/manifest.json', '/health', '/auth/trakt/status'):
            self.reply({'/manifest.json': _manifest_reply,
                        '/health': _health_page,
                        '/auth/trakt/status': _trakt_status}[p]())
            return True
        html = {'/': lambda: _landing_page(self.headers.get('Host') or f'127.0.0.1:{PORT}',
                                           bool(CFG.get('tls_cert'))),
                '/admin': _admin_page,
                '/configure': _configure_page,
                '/auth/trakt': _trakt_auth_page}.get(p)
        if html:
            self._html(html())
            return True
        return False
    # dispatcher HTTP: los muchos returns son la forma natural del router
    # pylint: disable=too-many-return-statements
    def do_GET(self):
        """Router GET: stream/catalog/meta/subtitles + páginas y redirects."""
        p = self._profile(urllib.parse.unquote(self.path.split('?', 1)[0]))  # sin query string
        if self._pages(p):
            return None
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

    def _html(self, body, code=200):
        b = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_POST(self):
        """Router POST: /login, /configure, /reload, /auth."""
        p = self.path.split('?', 1)[0]
        if p == '/login':                                        # fuera del gate: es el gate
            return self._login()
        if p in ('/reload', '/configure') and not self._authd():
            return self.reply({'err': 'forbidden'}, 403)
        if p == '/reload':
            load_config()
            _cache.clear()
            _meta_c.clear()
            _cat_c.clear()
            threading.Thread(target=refresh_manifests, daemon=True).start()
            safe = _cfg_public(CFG)
            log_ev('reload', cfg=safe)                            # nunca claves al log
            return self.reply({'ok': True, 'config': safe})       # ni a la respuesta
        if p == '/configure':
            _configure_save(self._form())
            return self._redir('/configure', code=303)
        return self.reply({'err': 'not found'}, 404)

    def _form(self):
        """Body urlencoded acotado (64KB) -> parse_qs."""
        try:
            ln = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            ln = 0
        return urllib.parse.parse_qs(self.rfile.read(max(0, min(ln, 65536))).decode())

    def _login(self):
        """POST /login: token correcto -> cookie HttpOnly + 302 a destino.
        El token viaja UNA vez en el body del POST — nunca en URL/historial."""
        form = self._form()
        tok = _TOK_SAFE.sub('', form.get('token', [''])[0])[:128]
        to = form.get('to', ['/admin'])[0]
        if to not in ('/admin', '/configure'):
            to = '/admin'                                        # no open-redirect
        real = CFG.get('admin_token') or ''
        if not real:
            return self._redir(to)                             # sin gate: dentro directamente
        if hmac.compare_digest(real, tok):
            self.send_response(302)
            self.send_header('Location', to)
            sec = '; Secure' if CFG.get('tls_cert') else ''      # https -> cookie solo-tls
            self.send_header('Set-Cookie',
                             f'conflux_auth={tok}; HttpOnly; SameSite=Strict; Path=/{sec}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        return self._html(_login_page(to, 'token incorrecto'), 401)





# claves que nunca salen en logs ni respuestas (key/token/secret/pass/admin)
_SECRET_RX = re.compile(r'key|token|secret|pass|admin', re.IGNORECASE)

_TOK_SAFE = re.compile(r'[^A-Za-z0-9._~+-]')            # cookie/header: solo charset de token


def _cfg_public(cfg):
    """config.json redactada para log/respuesta — tokens y claves se enmascaran."""
    return {k: ('***' if _SECRET_RX.search(k) else v) for k, v in cfg.items()}


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
    try:
        srv = Srv(('127.0.0.1', PORT), H)
    except OSError as e:
        # pythonw muere sin consola: sin esta línea un puerto ocupado no deja
        # rastro en conflux.log y el proceso viejo sigue sirviendo código stale
        log_ev('bind_fail', port=PORT, err=str(e))
        raise SystemExit(f'puerto {PORT} ocupado — ¿otra instancia viva? {e}')
    cert, key = CFG.get('tls_cert'), CFG.get('tls_key')
    if cert and key:                                   # TLS opcional (config.tls_cert/tls_key)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        log_ev('tls', cert=cert)
    if threading.current_thread() is threading.main_thread():
        def _bye(*_a):
            """SIGTERM (systemd stop): persiste el caché de manifests y sale limpio."""
            log_ev('shutdown')
            _atomic_json(_MF_CACHE, _persist_manifests())
            raise SystemExit(0)                   # no srv.shutdown(): deadlocked en el hilo principal
        signal.signal(signal.SIGTERM, _bye)
    srv.serve_forever()
