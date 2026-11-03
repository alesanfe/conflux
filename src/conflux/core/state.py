"""Estado compartido: config, cachés, health, pools y logging."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import json
import logging
import os
import threading
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logging.basicConfig(level=logging.INFO, format='%(message)s')



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
_SRC_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'sources.json')

try:
    _raw = json.load(_open(_SRC_FILE))
    if isinstance(_raw, list) and all(isinstance(s, dict) and s.get('name') and s.get('base') for s in _raw):
        SOURCES = [Source(s['name'], s['base']) for s in _raw]
except (OSError, ValueError) as e:
    _log.warning('sources.json ignorado (%s) — usando la lista embebida', e)


# ---- config.json unificado (opciones del usuario, todo opcional) ----
_CFG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'config.json')

_CFG_DEFAULTS = {'calendar_months_ahead': 2, 'trakt_client_id': '',
                 'm3u_path': '', 'debrid_provider': '', 'debrid_key': '', 'horizon_days': 45,
                 'stream_langs': ['es', 'latam', 'spanish', 'castellano', 'espanol'],
                 'sources_disabled': [], 'trakt_client_secret': '',
                 'torznab_url': '', 'torznab_key': '', 'mdblist_key': '',
                 'catalog_lists': '', 'source_order': [], 'catalogs_disabled': [],
                 'opensubtitles_key': '', 'admin_token': '', 'tls_cert': '', 'tls_key': '',
                 'trakt_access_token': '', 'trakt_refresh_token': '', 'trakt_expires': 0}

CFG: dict[str, Any] = dict(_CFG_DEFAULTS)

_DISABLED: set = set()


_SESS: dict = {}      # host -> Session (keep-alive: reusa TCP+TLS entre peticiones)

_sess_lock = threading.Lock()


# ---- estado: manifests de las fuentes (se refresca al arrancar) ----
manifests: list = []          # [dict manifest | None]


_MF_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), '.manifests-cache.json')


_cache: dict = {}            # (stype,sid) -> (ts, [streams])

_meta_c: dict = {}            # meta cacheada

_cat_c: dict = {}            # catálogo cacheado

HEALTH: dict = {}            # fuente -> {'ok':n,'fail':n,'last':ts,'err':str,'n':usable,'streak':n}

_fails: dict = {}            # host -> (strikes consecutivos, último ts) — disyuntor por host

HOST_OPEN  = 4            # strikes seguidos que abren el disyuntor

HOST_CD    = 60           # s de fail-fast con disyuntor abierto

_rtime: dict = {}            # ruta -> (n, total_ms, max_ms) — latencia del servidor

_rts: dict = {}            # ruta -> deque(ultimas 512 latencias) — histograma p50/p95/p99

_SERVED: dict = {}            # fuente -> {'n': streams aportados, 'res': {res: n}} — quién aporta de verdad

def _atomic_json(path, obj):
    """Escribe JSON de forma atómica (tmp+rename): un corte a mitad de write
    no corrompe el fichero — clave para config.json y el caché de manifests."""
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def log_ev(ev, **kv):
    """Un evento = una línea JSON — grep/journalctl -o json-pretty friendly."""
    _log.info(json.dumps({'ev': ev, **kv}, separators=(',', ':')))


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
    h['last'], h['err'], h['n'] = time.time(), err, n


def _cset(cache, key, val):
    if len(cache) > 400:
        for k, _ in sorted(cache.items(), key=lambda kv: kv[1][0])[:100]:
            cache.pop(k, None)
    cache[key] = (time.time(), val)


def _cget(cache, key):
    ent = cache.get(key)
    return ent[1] if ent and time.time() - ent[0] < CACHE_TTL else None


def as_dict(x):
    """x si es dict, {} si no — respuestas upstream degeneradas (JSON list/str/int)."""
    return x if isinstance(x, dict) else {}


STALE_MAX = 24*3600   # techo del stale-if-error: upstream muerto >24h = vacío


def _cstale(cache, key):
    """Último valor aunque caducado (max 24h): mejor dato viejo que 'Failed to fetch'."""
    ent = cache.get(key)
    return ent[1] if ent and time.time() - ent[0] < STALE_MAX else None

_YEARS  = [str(y) for y in range(time.gmtime().tm_year, time.gmtime().tm_year - 12, -1)]
