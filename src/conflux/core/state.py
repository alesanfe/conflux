"""Estado compartido: config, cachés, health, pools y logging."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import contextvars
import json
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_ROOT = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
         else os.path.dirname(os.path.dirname(os.path.dirname(
             os.path.dirname(os.path.abspath(__file__))))))
_LOG_FILE = os.environ.get('CONFLUX_LOG', os.path.join(_ROOT, 'conflux.log'))


class FakeClock:
    """Reloj determinista para tests: avanza solo cuando se le dice."""

    def __init__(self, t0=1_700_000_000.0):
        self._t = t0

    def time(self):
        return self._t

    def monotonic(self):
        return self._t

    def advance(self, seconds):
        self._t += seconds
        return self._t


class _Clock:
    """Singleton delegable (CONFLUX-003). Producción: sistema. Tests:
    `CLOCK.fake()` devuelve un FakeClock que gobierna todo el runtime;
    `CLOCK.reset()` restaura. Delegar (no reasignar) hace que los
    `from state import CLOCK` ya escritos vean el fake sin rebind."""

    def __init__(self):
        self._d = None                       # None -> sistema

    def fake(self, t0=1_700_000_000.0):
        self._d = FakeClock(t0)
        return self._d

    def set(self, clock):
        self._d = clock
        return clock

    def reset(self):
        self._d = None

    def time(self):
        return self._d.time() if self._d is not None else time.time()

    def monotonic(self):
        return self._d.monotonic() if self._d is not None else time.monotonic()


CLOCK = _Clock()


# ---- logging: nivel + rotación + redacción de secretos + correlation id ----

_REQ_ID: contextvars.ContextVar = contextvars.ContextVar('req_id', default='')

LOG_MAX_BYTES = 5 * 1024 * 1024          # 5 MiB por fichero
LOG_BACKUPS = 5                           # 5 copias rotadas
_LEVELS = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING,
           'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}

# claves de config cuyo valor NUNCA puede aparecer en logs
_SECRET_KEYS = {'debrid_key', 'torznab_key', 'torzapi_key', 'mdblist_key',
                'rpdb_key', 'opensubtitles_key', 'opensubtitles_pass',
                'admin_token', 'newznab_key', 'sabnzbd_key', 'easynews_pass',
                'nntp_pass', 'authkey', 'stremio_authkey'}
_TOKENISH = re.compile(r'(?i)(authKey|api_key|apikey|token|passwd|password'
                       r'|secret|bearer)[\s"\':=]+[A-Za-z0-9+/=_\-.]{8,}')


def _scrub(text):
    """Redacción central: secreto configurado -> ***, token-ish -> ***."""
    s = str(text)
    for k in _SECRET_KEYS:
        v = CFG.get(k)                       # CFG existe tras load_config
        if isinstance(v, str) and len(v) >= 6:
            s = s.replace(v, '***')
    return _TOKENISH.sub(lambda m: (m.group(1) + '=***') if m.group(1) else '***', s)


class _RedactFilter(logging.Filter):
    """Aplica _scrub al mensaje formateado de TODOS los handlers."""

    def filter(self, record):
        try:
            record.msg = _scrub(record.getMessage())
            record.args = ()
        except Exception:                    # nunca bloquear un log
            pass
        return True


def _log_level():
    """Precedencia: CONFLUX_LOG_LEVEL env > config.json log_level > INFO.
    (--log-level de CLI llama a set_log_level, que gana a todo.)"""
    raw = (os.environ.get('CONFLUX_LOG_LEVEL') or '').strip().upper()
    if raw in _LEVELS:
        return _LEVELS[raw]
    cfg = globals().get('CFG') or {}                # CFG nace tras _setup_logging()
    raw = str(cfg.get('log_level') or '').strip().upper()
    return _LEVELS.get(raw, logging.INFO)


def set_log_level(name):
    """Cambia el nivel del root logger en caliente (CLI --log-level)."""
    lvl = _LEVELS.get(str(name).strip().upper())
    if lvl is None:
        raise ValueError(f'nivel de log inválido: {name!r} '
                         f'(válidos: {", ".join(_LEVELS)})')
    logging.getLogger().setLevel(lvl)
    return lvl


def apply_config_level():
    """Tras load_config(): aplica env > config > INFO (si CLI no fijó nivel)."""
    logging.getLogger().setLevel(_log_level())


def _setup_logging(level=None):
    """Handlers propios marcados `_conflux` — idempotente y robusto aunque otro
    framework (pytest, embedders) ya haya configurado el root logger: no
    dependemos de basicConfig, que es no-op si root ya tiene handlers."""
    root = logging.getLogger()
    if not any(getattr(h, '_conflux', False) for h in root.handlers):
        handlers: list[logging.Handler] = []
        if sys.stderr is not None:                # consola si la hay (pythonw: None)
            handlers.append(logging.StreamHandler())
        try:
            os.makedirs(os.path.dirname(_LOG_FILE) or '.', exist_ok=True)
            handlers.append(logging.handlers.RotatingFileHandler(
                _LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                encoding='utf-8'))
        except OSError:                           # log bloqueado: solo consola
            pass
        if not handlers:
            handlers = [logging.NullHandler()]
        redact = _RedactFilter()
        for h in handlers:
            h._conflux = True                     # type: ignore[attr-defined]
            h.addFilter(redact)
            h.setFormatter(logging.Formatter('%(message)s'))
            root.addHandler(h)
        if not root.handlers:
            logging.basicConfig(format='%(message)s')
    root.setLevel(level or _log_level())
    return root


_setup_logging()



def _open(*a, **kw):
    """open() auto-cerrado al primer read()/write() — para one-liners tipo
    json.load(...) / .write(...) sin `with`."""
    if 'b' not in str(a[1] if len(a) > 1 else ''):
        kw.setdefault('encoding', 'utf-8')
    f = open(*a, **kw)  # noqa: SIM115 # pylint: disable=unspecified-encoding
    # helper autocierre; encoding va en kw por defecto
    class _W:
        def read(self):
            """Cierra f y devuelve el contenido."""
            with f:
                return f.read()
        def write(self, s):
            """Escribe s en f y lo cierra."""
            with f:
                return f.write(s)
    return _W()

_log = logging.getLogger('stremio-proxy')


STREAM_TIMEOUT = 4      # por fuente

STREAM_BUDGET  = 3.2    # respuesta máx al cliente (Stremio corta ~5s)

CACHE_TTL      = 600    # 10 min


_POOL    = ThreadPoolExecutor(max_workers=32)   # trabajo que sirve requests (meta/catalog/streams)

_POOL_BG = ThreadPoolExecutor(max_workers=12)   # bulkhead aparte: refresh, prefetch, feed sintético


PORT = 9700

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0'}


Source = namedtuple('Source', 'name base')   # DTO: .name/.base en vez de [0]/[1]


# addons de streams absorbidos por el proxy
SOURCES = [
    Source('Cinemeta',   'https://v3-cinemeta.strem.io'),   # metas canónicos + Popular/New/Featured
    Source('Torrentio',  'https://torrentio.strem.fun/language=spanish'),
    Source('TorrentsDB', 'https://torrentsdb.com'),
    Source('Peerflix',   'https://addon.peerflix.mov'),
    Source('Meteor',     'https://meteorfortheweebs.midnightignite.me'),
    Source('Ytztvio',    'https://ytztvio.galacticcapsule.workers.dev'),
    Source('NoTorrent',  'https://addon.notorrent2.workers.dev'),
    # Orion requiere URL configurada (lleva token de API): sources.json o env
    Source('Orion',      os.environ.get('STREMIO_ORION_URL',
                         'https://5a0d1888fa64-orion.baby-beamup.club')),
    Source('PublicDom',  'https://caching.stremio.net/publicdomainmovies.now.sh'),
    # descubrimiento de anime: solo catálogo+meta (sin stream → skip gratis en collect)
    Source('Kitsu',      'https://anime-kitsu.strem.fun'),
    Source('YouTube',    'https://v3-channels.strem.io'),
    Source('IPTV',       'https://ip-tv.hatip.dev'),
    Source('AllIPTV',    'https://fun.lnpx.workers.dev'),
    Source('Stravo',     'https://stravo.site/local'),
    Source('StravoII',   'https://v2.stravo.site/local'),
    Source('Hyper',      'https://hyper.stravo.site/local'),
    Source('Fenix',      'https://fenixflix.fenixhub.online'),
    Source('AniSeason',  'https://victorgveloso.github.io/animes-season-addon'),
    Source('TorrentCat', 'https://torrent-catalogs.strem.fun'),
    # oficial mantenido (onepace.arl.sh): la instancia koyeb dormía y daba
    # timeouts; froststream.cloutteam.com devuelve 403 siempre — fuera
    Source('OnePace',    'https://onepace.arl.sh'),
    Source('IMDBCat',    'https://1fe84bc728af-imdb-catalogs.baby-beamup.club'),
    Source('RTomatoes',  'https://7a82163c306e-rottentomatoes.baby-beamup.club'),
    Source('TMDB',       'https://tmdb.elfhosted.com'),
]


# fuentes externalizadas: sources.json (junto al proxy) sobreescribe la lista —
# [ {"name": ..., "base": ...}, ... ]. Config fuera del código (12-factor).
_SRC_FILE = os.path.join(_ROOT, 'sources.json')

try:
    _raw = json.load(_open(_SRC_FILE))
    if isinstance(_raw, list) and all(isinstance(s, dict) and s.get('name') and s.get('base') for s in _raw):
        SOURCES = [Source(s['name'], s['base']) for s in _raw]
except (OSError, ValueError) as e:
    _log.warning('sources.json ignorado (%s) — usando la lista embebida', e)

_SOURCES_BASE = list(SOURCES)      # embebida+sources.json; extra_sources va ENCIMA


# ---- config.json unificado (opciones del usuario, todo opcional) ----
_CFG_FILE = os.path.join(_ROOT, 'config.json')

_CFG_DEFAULTS = {'m3u_path': '', 'debrid_provider': '', 'debrid_key': '',
                 'stream_langs': ['es', 'latam', 'spanish', 'castellano', 'espanol'],
                 'sources_disabled': [],
                 'torznab_url': '', 'torznab_key': '', 'mdblist_key': '',
                 'catalog_lists': '', 'source_order': [], 'catalogs_disabled': [],
                 'stream_exclude': [], 'rpdb_key': '',
                 'rpdb_style': 'poster-default', 'rpdb_fallback': True,
                 'opensubtitles_key': '', 'opensubtitles_user': '', 'opensubtitles_pass': '',
                 'admin_token': '', 'tls_cert': '', 'tls_key': '',
                 'stream_max_gb': 0, 'stream_prefetch': True,
                 'stream_max_chars': 200,
                 'stream_sort': [], 'stream_required': [], 'stream_format': '',
                 'zilean_url': '', 'debrid_proxy': False,
                 'newznab_url': '', 'newznab_key': '',
                 'easynews_user': '', 'easynews_pass': '', 'parental': False,
                 'extra_sources': [], 'stream_preferred': [], 'source_timeout': 0,
                 'mediafusion': True, 'torrent_health': True,
                 'epg_url': 'https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz',
                 'debrid_providers': {}, 'stream_exclude_regex': [],
                 'stream_required_regex': [], 'source_groups': {},
                 'nntp_host': '', 'nntp_port': 563, 'nntp_user': '', 'nntp_pass': '',
                 'nntp_ssl': True, 'nntp_connections': 5,
                 'scrapers': ['yts', 'eztv', 'nyaa', 'tpb'], 'torzapi_key': '',
                 'max_connections': 64,
                 'sabnzbd_url': '', 'sabnzbd_key': '', 'scraper_rss': [],
                 'log_level': '', 'config_version': 1,
                 'stremio_authkey': '', 'authkey_scrape': True,
                 'auto_update': False, 'update_channel': 'stable',
                 'stream_light': True, 'iptv_health': True}

CFG: dict[str, Any] = dict(_CFG_DEFAULTS)

_DISABLED: set = set()


_SESS: dict = {}      # host -> Session (keep-alive: reusa TCP+TLS entre peticiones)

_sess_lock = threading.Lock()


# ---- estado: manifests de las fuentes (se refresca al arrancar) ----
manifests: list = []          # [dict manifest | None]


_MF_CACHE = os.path.join(_ROOT, '.manifests-cache.json')


_cache: dict = {}            # (stype,sid) -> (ts, [streams])

_meta_c: dict = {}            # meta cacheada

_cat_c: dict = {}            # catálogo cacheado
_DEAD_OPT: set = set()       # (cid, genre_lower) declaradas upstream pero siempre vacías
                             # ('xxx' en iptv-all) -> no se emiten; piden = catálogo base

HEALTH: dict = {}            # fuente -> {'ok':n,'fail':n,'last':ts,'err':str,'n':usable,'streak':n}

_fails: dict = {}            # host -> (strikes consecutivos, último ts) — disyuntor por host

HOST_OPEN  = 4            # strikes seguidos que abren el disyuntor

HOST_CD    = 60           # s de fail-fast con disyuntor abierto

_rtime: dict = {}            # ruta -> (n, total_ms, max_ms) — latencia del servidor

_rts: dict = {}            # ruta -> deque(ultimas 512 latencias) — histograma p50/p95/p99

_SERVED: dict = {}            # fuente -> {'n': streams aportados, 'res': {res: n}} — quién aporta de verdad

def _atomic_json(path, obj):
    """Escribe JSON de forma atómica y durable (CONFLUX-043): tmp en el mismo
    filesystem + flush+fsync + os.replace + fsync del directorio. Un corte en
    cualquier punto deja el fichero anterior intacto en disco."""
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:                                   # dirsync: el rename también sobrevive
        dfd = os.open(os.path.dirname(path) or '.', os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:                        # fsync de dir no existe en win32/algunos fs
        pass


# ---- veredictos de salud persistidos (META-001/003, RES-001) ----
# Contrato compartido por _URL_BAD/_URL_OK (streams) y _POSTER_BAD/_POSTER_OK
# (posters): bad = {url: (strikes, ts)}, ok = {url: ts}. Se guarda un fichero
# JSON por mapa, atómico vía _atomic_json. Los lectores ya ignoran entradas
# más viejas que su TTL, así que solo se guardan veredictos aún accionables.
# Multi-proceso: NO soportado (un solo proceso Conflux por instalación —
# dos instancias escribiendo el mismo fichero pisarían veredictos).
_HEALTH_KEEP = 1800 * 4        # conserva veredictos hasta 2h (reintento 30min x4)
_HEALTH_SAVE: dict = {}        # path -> ts del último save (throttle por fichero)


def health_load(path, bad, ok, keep=None):
    """Rellena bad/ok in-situ desde el fichero persistido (si existe y no está
    corrupto). Entradas caducadas o malformadas se descartan silenciosamente:
    un veredicto perdido solo repite un HEAD, nunca rompe el arranque.
    `keep` (default _HEALTH_KEEP=2h) debe >= la ventana del dominio — IPTV
    cuarentena 6h: con el default la entrada se olvidaba a las 2h."""
    keep = keep or _HEALTH_KEEP
    try:
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        now = CLOCK.time()
        for u, v in (data.get('bad') or {}).items():
            try:
                st, ts = int(v[0]), float(v[1])
            except (TypeError, ValueError, IndexError):
                continue
            if now - ts < keep:
                bad[u] = (st, ts)
        for u, ts in (data.get('ok') or {}).items():
            if isinstance(ts, (int, float)) and now - ts < keep:
                ok[u] = float(ts)
    except Exception:
        pass


def health_save(path, bad, ok, throttle=60, keep=None):
    """Persiste {bad, ok} atómicamente con throttle (los workers HEAD pueden
    terminar en ráfaga — un save por veredicto haría churn de disco).
    Solo se guardan veredictos aún vigentes: el fichero no crece con basura."""
    keep = keep or _HEALTH_KEEP
    now = CLOCK.time()
    if now - _HEALTH_SAVE.get(path, 0.0) < throttle:
        return
    _HEALTH_SAVE[path] = now
    try:
        _atomic_json(path, {
            'bad': {u: list(v) for u, v in bad.items()
                    if isinstance(v, (list, tuple)) and len(v) == 2
                    and now - v[1] < keep},
            'ok': {u: ts for u, ts in ok.items()
                   if isinstance(ts, (int, float)) and now - ts < keep},
        })
    except Exception:
        pass


def _pct(vals, q):
    if not vals:
        return 0
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * q))], 1)

COOLDOWN: dict = {}            # fuente -> (ts, motivo) hasta el que se salta

COOLDOWN_T = 12*3600      # cuota agotada: 12h

FAIL_CD_T  = 15*60        # racha de fallos: 15 min y reintenta

FAIL_STREAK= 3


def _health(name, ok, err='', n=0):
    h = HEALTH.setdefault(name, {'ok': 0, 'fail': 0, 'last': 0, 'err': '', 'n': 0, 'streak': 0})
    h['ok' if ok else 'fail'] += 1
    h['streak'] = 0 if ok else h.get('streak', 0) + 1
    h['last'], h['err'], h['n'] = CLOCK.time(), err, n


def _cset(cache, key, val):
    if len(cache) > 400:
        for k, _ in sorted(cache.items(), key=lambda kv: kv[1][0])[:100]:
            cache.pop(k, None)
    cache[key] = (CLOCK.time(), val)


def _cget(cache, key):
    ent = cache.get(key)
    return ent[1] if ent and CLOCK.time() - ent[0] < CACHE_TTL else None


def _meta_title(stype, sid):
    """(imdb, título, sXXeYY|None) — del meta cacheado; t='' si aún no está."""
    imdb = sid.split(':')[0]
    meta = (_cget(_meta_c, (stype, imdb)) or {}).get('meta') or {}
    t = (meta.get('name') or meta.get('title') or '').strip()
    parts = sid.split(':')
    se = None
    if len(parts) >= 3 and all(x.isdigit() for x in parts[1:3]):
        se = f's{int(parts[1]):02d}e{int(parts[2]):02d}'
    return imdb, t, se


def as_dict(x):
    """x si es dict, {} si no — respuestas upstream degeneradas (JSON list/str/int)."""
    return x if isinstance(x, dict) else {}


STALE_MAX = 24*3600   # techo del stale-if-error: upstream muerto >24h = vacío


def _cstale(cache, key):
    """Último valor aunque caducado (max 24h): mejor dato viejo que 'Failed to fetch'."""
    ent = cache.get(key)
    return ent[1] if ent and CLOCK.time() - ent[0] < STALE_MAX else None

_YEARS  = [str(y) for y in range(time.gmtime().tm_year, time.gmtime().tm_year - 12, -1)]


def new_req_id():
    """Id de correlación por request (8 hex); lo fija el handler HTTP."""
    import secrets
    _REQ_ID.set(secrets.token_hex(4))
    return _REQ_ID.get()


def log_ev(ev, **kv):
    """Un evento = una línea JSON — grep/journalctl -o json-pretty friendly."""
    rid = _REQ_ID.get()
    if rid:
        kv.setdefault('rid', rid)
    _log.info(json.dumps({'ev': ev, **kv}, separators=(',', ':')))


# ---- AppContext (CONFLUX-001): el estado mutable agrupado e inyectable ----
# Los nombres de módulo son referencias a los contenedores del contexto por
# defecto (composition root): la mutación in-situ sigue siendo la misma, y un
# contexto aislado en tests no comparte nada con éste.
from conflux.core.ctx import AppContext            # noqa: E402

CTX = AppContext()
CTX.sources.sources = SOURCES
CTX.sources.manifests = manifests
CTX.sources.disabled = _DISABLED
CTX.failures.health = HEALTH
CTX.breakers.fails = _fails
CTX.breakers.cooldown = COOLDOWN
CTX.breakers.dead_opt = _DEAD_OPT
CTX.metrics.rtime = _rtime
CTX.metrics.rts = _rts
CTX.metrics.served = _SERVED
CTX.cfg = CFG
CTX.caches['stream'] = _cache
CTX.caches['meta'] = _meta_c
CTX.caches['catalog'] = _cat_c
CTX.sessions = _SESS
CTX.sessions_lock = _sess_lock
CTX.register_pool(_POOL)
CTX.register_pool(_POOL_BG)
