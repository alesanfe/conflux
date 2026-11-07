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
import socket
import ssl
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import orjson                                   # ~7x json.dumps en replies
                                                # grandes + UTF-8 nativo
from conflux.app.bg import _prefetch_catalogs, _refresher
from conflux.app.meta import handle_meta
from conflux.app.stream import handle_stream
from conflux.app.subs import _sub_download, handle_subtitles
from conflux.core.cfg import _PROF, _profile_load, load_config
from conflux.core.state import (
    _MF_CACHE,
    _REQ_ID,
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
    new_req_id,
)
from conflux.domain.catalog import handle_catalog
from conflux.domain.sources import _load_manifests_cache, _persist_manifests, refresh_manifests
from conflux.infra.debrid import _db_resolve, _db_stream, _db_usenet
from conflux.infra.newznab import _sab_add
from conflux.web.metrics import _diag_json, _metrics_text
from conflux.web.pages import (
    _admin_page,
    _configure_page,
    _configure_save,
    _health_page,
    _landing_page,
    _login_page,
    _manifest_reply,
)
from conflux.web.torzapi import handle_torzapi

_dumps = orjson.dumps


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
        body = _dumps(obj)
        gz = len(body) > 1024 and 'gzip' in self.headers.get('Accept-Encoding', '')
        if gz:
            # level 1: ~2x más rápido que 6/9 — el cliente va por loopback,
            # el tamaño extra no cuesta nada y la CPU sí (streams de ~0.5MB).
            body = gzip.compress(body, compresslevel=1)
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')     # respuestas dinámicas
        self.send_header('X-Request-Id', _REQ_ID.get() or '-')
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
    def _auth_fail(self):
        """Un intento _authd fallido -> cuenta contra la IP; True cuando ya
        excede el rate (SEC-002): sin esto el header X-Admin-Token era
        brute-forceable sin pasar por el rate-limit de /login."""
        ip = getattr(self, 'client_address', ('local',))[0]
        now = time.time()
        if len(_AUTH_TRIES) > 256:                      # acotado: spam multi-IP
            for ip_, dq in list(_AUTH_TRIES.items()):   # no infla el dict —
                if not dq:                              # las vacías se reciclan
                    _AUTH_TRIES.pop(ip_, None)
        tries = _AUTH_TRIES.setdefault(ip, deque())
        while tries and now - tries[0] > 60:
            tries.popleft()
        tries.append(now)
        if len(tries) > 10:
            log_ev('auth_ratelimit', ip=ip, n=len(tries))
            return True
        return False
    def _auth_gate(self):
        """_authd con rate-limit: None ok | 'fail' responde caller | 'limit' ya
        respondió 429. Con admin_token vacío nunca cuenta (gate desactivado)."""
        if self._authd():
            return None
        if self._auth_fail():
            self.reply({'err': 'too many auth attempts',
                        'code': 'CFX-AUTH-011'}, 429)
            return 'limit'
        return 'fail'
    # claves permitidas en la cfg de la URL (nada de paths/URLs -> SSRF;
    # nada de admin/tls/keys de servidor -> fuera de scope)
    _CFG_URL_KEYS = frozenset({
        'stream_langs', 'stream_exclude', 'rpdb_key',
        'catalogs_disabled', 'sources_disabled', 'source_order',
        'debrid_provider', 'debrid_key',
        'stream_max_gb', 'stream_prefetch', 'stream_required', 'stream_sort',
        'stream_format', 'debrid_proxy', 'parental', 'stream_preferred',
        'source_timeout', 'mediafusion', 'torrent_health',
        'opensubtitles_key', 'opensubtitles_user', 'opensubtitles_pass', 'mdblist_key',
        'debrid_providers', 'stream_exclude_regex', 'stream_required_regex',
        'source_groups', 'nntp_host', 'nntp_port', 'nntp_user', 'nntp_pass',
        'nntp_ssl', 'nntp_connections', 'scrapers'})
    _CFG_URL_LIST = frozenset({'stream_langs', 'stream_exclude', 'catalogs_disabled',
                               'sources_disabled', 'source_order', 'stream_required',
                               'stream_sort', 'stream_preferred',
                               'stream_exclude_regex', 'stream_required_regex', 'scrapers'})
    _CFG_URL_NUM = {'stream_max_gb': (0, 100000), 'source_timeout': (0, 30),
                    'nntp_port': (1, 65535), 'nntp_connections': (1, 64)}
    _CFG_URL_BOOL = frozenset({'stream_prefetch', 'debrid_proxy', 'parental',
                               'mediafusion', 'torrent_health', 'nntp_ssl'})
    _CFG_URL_DICT = frozenset({'debrid_providers'})   # str -> str
    _CFG_URL_DLIST = frozenset({'source_groups'})     # str -> list[str]

    @classmethod
    def _cfg_url_ok(cls, k, v):
        """Whitelist + tipos: un list-clave como int/str rompe los patrones
        (itera chars/int); un int-clave como str contamina config."""
        if k not in cls._CFG_URL_KEYS:
            return False
        if k in cls._CFG_URL_LIST:
            return isinstance(v, list) and all(isinstance(x, str) for x in v)
        if k in cls._CFG_URL_DICT:
            return isinstance(v, dict) and all(
                isinstance(a, str) and isinstance(b, str) for a, b in v.items())
        if k in cls._CFG_URL_DLIST:
            return isinstance(v, dict) and all(
                isinstance(a, str) and isinstance(b, list)
                and all(isinstance(x, str) for x in b) for a, b in v.items())
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
        if p in ('/admin', '/configure', '/admin/diag'):
            g = self._auth_gate()
            if g == 'limit':
                return True                                     # 429 ya enviado
            if g == 'fail':
                self._html(_login_page(p), 401)                 # form login -> cookie HttpOnly
                return True
            if p == '/admin/diag':                              # OBS-008/009: snapshot JSON
                self.reply(_diag_json())
                return True
        if p == '/health/live':                                 # proceso responde (cheap)
            self.reply({'status': 'alive', 'version': _version()})
            return True
        if p == '/health/ready':                                # puede servir (fuentes/caches)
            self.reply(_ready_page())
            return True
        if p in ('/manifest.json', '/health'):
            self.reply({'/manifest.json': _manifest_reply,
                        '/health': _health_page}[p]())
            return True
        html = {'/': lambda: _landing_page(self.headers.get('Host') or f'127.0.0.1:{PORT}',
                                           bool(CFG.get('tls_cert'))),
                '/admin': _admin_page,
                '/configure': _configure_page}.get(p)
        if html:
            self._html(html())
            return True
        return False
    # dispatcher HTTP: los muchos returns son la forma natural del router
    # pylint: disable=too-many-return-statements
    def do_GET(self):
        """Router GET: stream/catalog/meta/subtitles + páginas y redirects."""
        new_req_id()                                            # correlation id por request
        try:
            return self._route_get()
        except Exception as e:                                  # noqa: BLE001 — última barrera:
            log_ev('route_err', path=getattr(self, 'path', '?')[:120],  # una ruta rota
                   err=repr(e)[:200])                           # NO debe colgar sin respuesta
            try:
                self.reply({'err': 'internal'}, 500)            # 500 -> el cliente reintenta
            except Exception:                                   # noqa: BLE001 — conexión ya rota
                pass
            return None

    def _route_get(self):
        """Cuerpo del router GET (bajo el boundary de do_GET)."""
        p = self._profile(urllib.parse.unquote(self.path.split('?', 1)[0]))  # sin query string
        if self._pages(p):
            return None
        if p.startswith('/r/'):                                        # debrid redirect
            seg = p.split('/')
            pvd, h = (seg[2], seg[3]) if len(seg) == 4 else ('', '')
            fi = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('fi', ['0'])[0]
            return self._redir(_db_resolve(pvd, h, fi))
        if p.startswith('/dp/'):                                       # debrid proxy (túnel de salida)
            seg = p.split('/')
            pvd, h = (seg[2], seg[3]) if len(seg) == 4 else ('', '')
            fi = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('fi', ['0'])[0]
            return self._db_proxy(pvd, h, fi)
        if p.startswith('/nzb/'):                                      # usenet (newznab -> torbox)
            b = p[5:]
            try:
                link = base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode()
            except (ValueError, UnicodeDecodeError, binascii.Error):
                link = ''
            return self._redir(_db_usenet(link)
                               if link.startswith('http') else None)
        if p.startswith('/sab/'):                                      # SABnzbd addurl -> UI del downloader
            b = p[5:]
            try:
                link = base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode()
            except (ValueError, UnicodeDecodeError, binascii.Error):
                link = ''
            return self._redir(_sab_add(link) if link.startswith('http') else None)
        if p.startswith('/en/'):                                       # easynews: creds sólo en el redirect
            b = p[4:]
            try:
                link = base64.urlsafe_b64decode(b + '=' * (-len(b) % 4)).decode()
            except (ValueError, UnicodeDecodeError, binascii.Error):
                link = ''
            u, pw = CFG.get('easynews_user', ''), CFG.get('easynews_pass', '')
            try:
                sp = urllib.parse.urlsplit(link)
            except ValueError:                     # b64 arbitrario -> url rota
                sp = None
            if link and u and pw and link.startswith('http') and sp and \
                    sp.hostname and (sp.hostname == 'easynews.com'
                                     or sp.hostname.endswith('.easynews.com')):
                cred = f'{urllib.parse.quote(u, safe="")}:{urllib.parse.quote(pw, safe="")}@'
                link = f'{sp.scheme}://{cred}{sp.netloc}{sp.path}'
            else:
                link = ''               # sin creds u host ajeno: las credenciales
                                        # easynews NUNCA viajan a un host arbitrario
            return self._redir(link if link.startswith('http') else None)
        if p.startswith('/s/'):                                        # subtitle redirect
            return self._redir(_sub_download(p.rsplit('/', 1)[-1]))
        if p in ('/torznab/api', '/newznab/api'):                      # indexer *arr (Torznab/Newznab)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code, body = handle_torzapi(q)
            return self._xml(body, code)
        if p == '/metrics':                                            # Prometheus scrape
            return self._text(_metrics_text())
        return self._timed_route(p)
    def _db_proxy(self, pvd, h, fi):
        """Túnel de salida debrid (config.debrid_proxy): el player recibe los
        bytes desde nuestro socket — la IP que ve el debrid es la del servidor.
        Pasa Content-* del upstream; Range ya viaja en _db_stream."""
        r = _db_stream(pvd, h, fi, self.headers)
        if r is None:
            return self._redir(None)
        try:
            self.send_response(r.status_code)
            for k in ('Content-Type', 'Content-Length', 'Content-Range',
                      'Accept-Ranges', 'Content-Disposition'):
                if r.headers.get(k):
                    self.send_header(k, r.headers[k])
            self.end_headers()
            for chunk in r.iter_content(1 << 16):                   # 64KB — sin buffering del fichero
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass                                                    # cliente desconectado a mitad
        finally:
            r.close()
        return None

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
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def _xml(self, body, code=200):
        b = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/rss+xml; charset=utf-8')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def _text(self, body, code=200):
        b = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_POST(self):
        """Router POST: /login, /configure, /reload."""
        new_req_id()
        try:
            return self._route_post()
        except Exception as e:                                  # noqa: BLE001 — última barrera:
            log_ev('route_err', path=getattr(self, 'path', '?')[:120],  # misma regla que GET:
                   err=repr(e)[:200])                           # nunca colgar sin responder
            try:
                self.reply({'err': 'internal'}, 500)
            except Exception:                                   # noqa: BLE001 — conexión ya rota
                pass
            return None

    def _route_post(self):
        """Cuerpo del router POST (bajo el boundary de do_POST)."""
        p = self.path.split('?', 1)[0]
        if self.headers.get('Transfer-Encoding'):                # smuggling: CL único y acotado
            return self.reply({'err': 'transfer-encoding not supported',
                               'code': 'CFX-HTTP-001'}, 400)
        ct = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if ct not in ('', 'application/x-www-form-urlencoded'):  # solo forms urlencoded
            return self.reply({'err': 'unsupported content-type',
                               'code': 'CFX-HTTP-002'}, 415)
        if p == '/login':                                        # fuera del gate: es el gate
            return self._login()
        if p in ('/reload', '/configure'):
            g = self._auth_gate()
            if g == 'limit':
                return                                          # 429 ya enviado
            if g == 'fail':
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
        El token viaja UNA vez en el body del POST — nunca en URL/historial.
        Rate limit por IP: 10 intentos/min (fuerza bruta local)."""
        ip = getattr(self, 'client_address', ('local',))[0]
        now = time.time()
        tries = _LOGIN_TRIES.setdefault(ip, deque())
        while tries and now - tries[0] > 60:
            tries.popleft()
        if len(tries) >= 10:
            return self.reply({'err': 'too many login attempts',
                               'code': 'CFX-AUTH-010'}, 429)
        tries.append(now)
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

_LOGIN_TRIES: dict = {}                                 # ip -> deque(ts) — rate limit /login

_AUTH_TRIES: dict = {}                                  # ip -> deque(ts) — fallos header/cookie
_STARTED = time.time()


def _version():
    """Versión del paquete (pyproject) con fallback seguro."""
    try:
        import importlib.metadata
        return importlib.metadata.version('conflux')
    except Exception:
        return '0+local'


def _ready_page():
    """Readiness (CONFLUX-026): fuentes con manifest + breakers abiertos."""
    from conflux.core.state import _fails, HEALTH, COOLDOWN
    now = time.time()
    src_ok = sum(1 for m in manifests if m)
    open_brk = [h for h, (st_, ts) in _fails.items()
                if st_ >= 4 and now - ts < 60]
    degraded = [n for n, h in HEALTH.items() if h.get('streak', 0) >= 3]
    status = 'ready' if src_ok else 'starting'
    if open_brk or degraded:
        status = 'degraded'
    return {'status': status, 'version': _version(),
            'uptime_s': round(now - _STARTED, 1),
            'sources': {'total': len(SOURCES), 'with_manifest': src_ok,
                        'degraded': sorted(degraded)[:20]},
            'breakers_open': sorted(open_brk),
            'cooldowns': len(COOLDOWN),
            'cfg_ok': bool(CFG), 'ts': int(now)}


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

class _Srv(ThreadingHTTPServer):
    """Hilos daemon + pool acotado: >MAX_CONNS conexiones esperan en el
    backlog del kernel (un burst no crea hilos ilimitados)."""
    daemon_threads = True   # sin hilos zombie
    request_queue_size = 64              # backlog: el cliente espera, no muere
    _sem = threading.Semaphore(int(CFG.get('max_connections', 64)))

    def server_bind(self):
        """Windows: SO_REUSEADDR permite doble bind silencioso sobre el mismo
        puerto (≈SO_REUSEPORT) -> dos instancias sirven código distinto a la
        vez y `bind_fail` jamás dispara. SO_EXCLUSIVEADDRUSE lo prohíbe;
        en POSIX el kernel ya rechaza el segundo listen bind."""
        if sys.platform == 'win32':
            self.allow_reuse_address = False   # REUSEADDR tras EXCLUSIVE -> EINVAL
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        self._sem.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:                                  # noqa: BLE001 — spawn falló: liberar
            self._sem.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._sem.release()


def _launch_update_swap():
    """Update staged (auto_update): lanza el .ps1 que reemplaza el exe cuando
    el proceso muere (Windows no permite reemplazar un exe en ejecución)."""
    try:
        import os
        import subprocess
        from conflux.infra.updater import _SWAP_PS1, _UPD_JSON
        if getattr(sys, 'frozen', False) and os.path.exists(_UPD_JSON) \
                and os.path.exists(_SWAP_PS1):
            subprocess.Popen(
                ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-File', _SWAP_PS1],                          # noqa: S603 — script propio generado
                # getattr: CREATE_NO_WINDOW no existe en POSIX (mypy/linux)
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                if os.name == 'nt' else 0,
                close_fds=True)
            log_ev('update_swap_launched')
    except Exception:                                          # noqa: BLE001 — el swap no rompe el stop
        pass


def main():
    """Arranque: manifests cacheados si hay -> refresh bg; si no, fetch en vivo."""
    from conflux.core.cfg import ensure_admin_token
    ensure_admin_token()              # admin siempre con token (256b) desde el 1er arranque
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

    try:
        srv = _Srv(('127.0.0.1', PORT), H)
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
            """SIGTERM (systemd stop): persiste el caché de manifests; si hay
            update staged lanza el swap helper y sale limpio."""
            log_ev('shutdown')
            _atomic_json(_MF_CACHE, _persist_manifests())
            try:
                from conflux.app.obs import _hist_snapshot       # OBS-002
                _hist_snapshot()                                # rollup final del día
            except Exception:                                   # noqa: BLE001
                pass
            _launch_update_swap()
            raise SystemExit(0)                   # no srv.shutdown(): deadlocked en el hilo principal
        signal.signal(signal.SIGTERM, _bye)
    srv.serve_forever()
