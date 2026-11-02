#!/usr/bin/env python3
"""Proxy local para Stremio: unifica el formato de los streams de todos los
addons y pasa sus catálogos/metadatos. Escucha en 127.0.0.1:9700."""
import json, os, re, urllib.request, urllib.parse, threading, time, gzip, logging, sys
import requests
from collections import namedtuple, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

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
    Source('Frost',      'https://froststream.cloutteam.com'),
    Source('Fenix',      'https://fenixflix.fenixhub.online'),
    Source('AniSeason',  'https://victorgveloso.github.io/animes-season-addon'),
    Source('TorrentCat', 'https://torrent-catalogs.strem.fun'),
    Source('OnePace',    'https://onepaceaddon-zoropogger.koyeb.app'),
    Source('IMDBCat',    'https://1fe84bc728af-imdb-catalogs.baby-beamup.club'),
    Source('RTomatoes',  'https://7a82163c306e-rottentomatoes.baby-beamup.club'),
    Source('TMDB',       'https://tmdb.elfhosted.com'),
]

# fuentes externalizadas: sources.json (junto al proxy) sobreescribe la lista —
# [ {"name": ..., "base": ...}, ... ]. Config fuera del código (12-factor).
_SRC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sources.json')
try:
    _raw = json.load(open(_SRC_FILE))
    if isinstance(_raw, list) and all(isinstance(s, dict) and s.get('name') and s.get('base') for s in _raw):
        SOURCES = [Source(s['name'], s['base']) for s in _raw]
except Exception:
    pass

# ---- config.json unificado (opciones del usuario, todo opcional) ----
_CFG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
_CFG_DEFAULTS = {'calendar_months_ahead': 2, 'trakt_client_id': '', 'poster_ratings': False,
                 'm3u_path': '', 'debrid_provider': '', 'debrid_key': '', 'horizon_days': 45,
                 'stream_langs': ['es', 'latam', 'spanish', 'castellano', 'espanol'],
                 'sources_disabled': [], 'trakt_client_secret': '',
                 'trakt_access_token': '', 'trakt_refresh_token': '', 'trakt_expires': 0}
CFG = dict(_CFG_DEFAULTS)
_DISABLED = set()
def load_config():
    CFG.clear(); CFG.update(_CFG_DEFAULTS)                  # reset: borrar el archivo = defaults
    try:
        CFG.update({k: v for k, v in json.load(open(_CFG_FILE)).items() if k in CFG})
    except Exception:
        pass
    global _DISABLED
    _DISABLED = set(CFG.get('sources_disabled') or [])
def _save_cfg():
    try:
        open(_CFG_FILE, 'w').write(json.dumps(
            {k: v for k, v in CFG.items() if v or isinstance(v, list)}, indent=1))
    except OSError:
        pass
load_config()

_SESS      = {}      # host -> Session (keep-alive: reusa TCP+TLS entre peticiones)
_sess_lock = threading.Lock()

def _session(host):
    s = _SESS.get(host)
    if s is None:
        with _sess_lock:
            s = _SESS.setdefault(host, requests.Session())
            s.headers.update(UA)
            s.headers['Accept-Encoding'] = 'gzip'
    return s

def fetch(url, timeout=7):
    host = urllib.parse.urlsplit(url).netloc
    for att in range(2):                                        # 1 reintento con backoff: flakes transitorios
        try:
            r = _session(host).get(url, timeout=timeout, stream=True)
            try:
                body = r.raw.read(6_000_001, decode_content=True)   # gzip transparente
                if len(body) > 6_000_000: return None           # respuesta gigante: descartar
                return json.loads(body)
            finally:
                r.close()
        except Exception:
            _fails[host] = _fails.get(host, 0) + 1
            if not att: time.sleep(0.3)
    return None

# ---- estado: manifests de las fuentes (se refresca al arrancar) ----
manifests = []          # [dict manifest | None]

_MF_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.manifests-cache.json')

def _load_manifests_cache():
    """Manifests persistidos del arranque anterior: el proxy responde al instante
    y refresh_manifests() en background trae la versión fresca (ventana fría ~0)."""
    try:
        cached = json.load(open(_MF_CACHE))
        if len(cached) == len(SOURCES) and sum(1 for m in cached if m) >= len(SOURCES) // 2:
            return cached
    except Exception:
        pass
    return None

def refresh_manifests():
    global manifests
    fs = [_POOL_BG.submit(fetch, base + '/manifest.json', 10) for _, base in SOURCES]
    manifests = [f.result() for f in fs]
    retry = [(i, _POOL_BG.submit(fetch, SOURCES[i].base + '/manifest.json', 20))
             for i, m in enumerate(manifests) if not m]
    for i, f in retry:
        m = f.result()
        if m:
            manifests[i] = m                    # cold-start (vercel) al segundo intento
    try:
        with open(_MF_CACHE, 'w') as f:
            json.dump(manifests, f)
    except Exception:
        pass

def res_of(m):
    return [r if isinstance(r, str) else r.get('name') or r.get('id', '')
            for r in (m or {}).get('resources', [])]

def res_types(m, res):
    """Tipos declarados para un recurso; None = sin restricción declarada."""
    for r in (m or {}).get('resources', []):
        if isinstance(r, dict) and (r.get('name') or r.get('id')) == res:
            return set(r.get('types') or []) or None
    return None

def res_prefixes(m, res):
    """idPrefixes declarados para un recurso; None = sin restricción declarada."""
    for r in (m or {}).get('resources', []):
        if isinstance(r, dict) and (r.get('name') or r.get('id')) == res:
            return set(r.get('idPrefixes') or []) or None
    return None

# ---- normalizacion de streams ----
RE_RES   = re.compile(r'\b(4320p|2160p|1440p|1080p|720p|576p|480p|360p|240p|8K|4K|HD|SD)\b', re.I)
RE_TAGS  = re.compile(r'\b(HDR10\+?|HDR|Dolby\s?Vision|DV|WEB-?DL|WEBRip|BluRay|BDRip|Remux|HEVC|x265|x264|10bit|Hi10P|CAM|HDCAM|TS|SCR|DVD|IMAX|Open\s?Matte|REPACK|PROPER)\b', re.I)
RE_SIZE  = re.compile(r'(\d+(?:[.,]\d+)?)\s?(GB|GiB|MB|MiB|TB)\b', re.I)
RE_SEEDS = re.compile(r'(?:👤|seed(?:er)?s?|peers?)\s*[:=]?\s*(\d{1,6})|(\d{1,6})\s*(?:seed(?:er)?s?|peers?)\b', re.I)
RE_SRC   = re.compile(r'[⚙️🔎☁️]\s*([\w.\-]+)')
RE_LANG  = re.compile(r'\b(Multi\s?Audio|Multi|Dual\s?Audio|Dual|LATINO|Latino|CASTELLANO|Castellano|ESPAÑOL|Español|Spanish|SPANISH|ESP|ES|ENGLISH|English|ENG|EN|VOSE|FRENCH|French|GERMAN|ITALIAN|PORTUGUESE|JAPANESE|KOREAN|HINDI|RUS|Rutor)\b', re.I)
RE_CODEC = re.compile(r'\b(HEVC|x265|h265|x264|h264|AV1|VP9|AVC|XviD)\b', re.I)
RE_AUDIO = re.compile(r'\b(Dolby\s?Atmos|Atmos|DDP?5\.1|DD\+|TrueHD|DTS(?:-HD|-X)?|FLAC|AAC2?\.0|AAC|[257]\.1|Stereo|Mono)\b', re.I)
RE_DUR   = re.compile(r'(\d+)\s?h\s?(\d+)\s?m|(\d{1,2}):(\d{2}):\d{2}|(\d+)\s?min', re.I)
FLAGS    = re.compile(r'[\U0001F1E6-\U0001F1FF]{2}')
RE_ES    = re.compile(r'\b(LATINO|LATAM|CASTELLANO|ESPAÑOL|ESP|Spanish|SPANISH|VOSE|ES)\b', re.I)
RE_SRCT  = re.compile(r'\b(BDREMUX|REMUX|BluRay|BDRip|WEB-?DL|WEBRip|HDTV|DVDRip|DVD|CAM|HDCAM|TS|SCR)\b', re.I)
RE_HDR   = re.compile(r'\b(HDR10\+|Dolby\s?Vision|HDR10|HDR|DV)\b', re.I)

LANG_MAP = {'EN':'Inglés','ENG':'Inglés','ENGLISH':'Inglés','ES':'Español','ESP':'Español',
            'SPANISH':'Español','ESPAÑOL':'Español','CASTELLANO':'Castellano','LATINO':'Latino',
            'MULTI':'Multi','MULTI AUDIO':'Multi','DUAL':'Dual','DUAL AUDIO':'Dual',
            'JAPANESE':'Japonés','FRENCH':'Francés','GERMAN':'Alemán','ITALIAN':'Italiano',
            'PORTUGUESE':'Portugués','KOREAN':'Coreano','HINDI':'Hindi','RUS':'Ruso','RUTOR':'Ruso'}
AUDIO_MAP = {'ATMOS':'Atmos','DOLBY ATMOS':'Atmos','TRUEHD':'TrueHD','DDP5.1':'DD+ 5.1','DD5.1':'DD 5.1',
             'DD+':'DD+','DTS-HD':'DTS-HD','DTS-X':'DTS:X','DTS':'DTS','FLAC':'FLAC','AAC2.0':'AAC 2.0',
             'AAC':'AAC','STEREO':'Stereo','MONO':'Mono','2.1':'2.1','5.1':'5.1','7.1':'7.1'}
INDEXER_MAP = {'NYAA':'NyaaSi','NYAASI':'NyaaSi','RUTRACKER':'Rutracker','WOLFMAX4K':'Wolfmax4k',
 'FRAMESTOR':'FraMeSToR','YTS':'YTS','1337X':'1337x','EZTV':'EZTV','RARBG':'RARBG',
 'THEPIRATEBAY':'ThePirateBay','TPB':'ThePirateBay','TGX':'TGx','TORRENTGALAXY':'TGx',
 'KICKASS':'KickAss','HORRIBLESUBS':'HorribleSubs','ERAI-RAWS':'Erai-raws','BITSEARCH':'BitSearch',
 'PIXELDRAIN':'Pixeldrain','FASTPLAY':'FastPlay','ASTRA':'Astra','SPIDER':'Spider','FILEMOOD':'filemood',
 'RUTOR':'RuTor','MEGUSTA':'MeGusta','NTB':'NTb','FGT':'FGT','EVO':'EVO','PSA':'PSA','ION10':'ION10',
 'XNT':'XNT','DDR':'DDR','SUCCESSFULCRAB':'SuccessfulCrab','SMURF':'SMURF','VXT':'VxT'}
SRCT_ORD = ['BDREMUX','REMUX','BLURAY','WEB-DL','WEBRIP','BDRIP','HDTV','DVDRIP','DVD','CAM','HDCAM','TS','SCR']
HDR_ORD  = ['HDR10+','DV','HDR10','HDR']

def _prio(found, order):
    s = {f.upper().replace('DOLBY VISION','DV').replace('WEBDL','WEB-DL') for f in found}
    return next((o for o in order if o in s), '')

def parse_size(t):
    m = RE_SIZE.search(t)
    if not m: return None
    v = float(m.group(1).replace(',', '.'))
    u = m.group(2).upper()
    if u.startswith('M'): v /= 1024
    if u.startswith('T'): v *= 1024
    return v

def parse_seeds(t):
    m = RE_SEEDS.search(t)
    if not m: return None
    return int(next(g for g in m.groups() if g))

def extract_fields(s):
    """Stream crudo → dict de campos parseados (res, seeds, size, indexer…)."""
    bh   = s.get('behaviorHints') or {}
    raw  = (s.get('title') or s.get('description') or s.get('name') or '').strip()
    fname= bh.get('filename','')
    text = ' '.join(x for x in [raw, s.get('name',''), fname, bh.get('bingeGroup','')] if x)
    rm   = RE_RES.search(text)
    res  = rm.group(1).upper().replace('4K','2160P').replace('8K','4320P') if rm else 'SD'
    size = parse_size(text)
    if size is None and bh.get('videoSize'):
        size = bh['videoSize'] / 1e9
    seeds = parse_seeds(text)
    if isinstance(bh.get('seeders'), int) and not isinstance(bh.get('seeders'), bool):
        seeds = bh['seeders']                                          # Meteor: dato real
    srcm    = RE_SRC.search(text)
    indexer = next((g.strip() for g in srcm.groups() if g), None) if srcm else None
    fl    = list(dict.fromkeys(FLAGS.findall(raw)))
    if fl: lang = ' '.join(fl[:6])
    else:
        ls = [x for x in dict.fromkeys(LANG_MAP.get(g.upper(), '') for g in RE_LANG.findall(text)) if x]
        lang = 'Multi' if 'Multi' in ls or len(ls) > 2 else ' / '.join(ls)
    au = list(dict.fromkeys(AUDIO_MAP.get(x.upper(), x.upper().title()) for x in RE_AUDIO.findall(text)))
    au.sort(key=lambda a: 1 if re.fullmatch(r'[257]\.1', a) else 0)    # marca antes que canales
    codecm = RE_CODEC.search(text)
    durm   = RE_DUR.search(text)
    dur    = f"{durm.group(1)}h{durm.group(2)}m" if durm and durm.group(1) else (f"{durm.group(3)}:{durm.group(4)}" if durm and durm.group(3) else (f"{durm.group(5)}m" if durm else ''))
    if not dur and isinstance(bh.get('duration'), (int, float)) and bh['duration'] > 60:
        ds = int(bh['duration']); dur = f"{ds//3600}h{ds%3600//60}m"   # Meteor: duración real
    return {
        'res': res, 'disp': res[:-1]+'p' if res.endswith('P') else res,
        'size': size, 'seeds': seeds, 'indexer': indexer, 'text': text,
        'lang': lang, 'audio': ' '.join(au[:3]),
        'codec': codecm.group(1).upper() if codecm else '', 'dur': dur,
        'cached': bool(bh.get('cached') or '☁️' in text or 'debrid' in text.lower()),
        'srctype': _prio(RE_SRCT.findall(text), SRCT_ORD),
        'hdr': _prio(RE_HDR.findall(text), HDR_ORD),
        'es': bool(RE_ES.search(text)),
    }

_CODEC_TOKENS = {'X265','X264','H265','H264','HEVC','AV1','VP9','AVC','10BIT','AAC','DTS'}

def _indexer_of(f, short):
    idx = f['indexer'] or short
    if idx.upper() in _CODEC_TOKENS: idx = short
    if idx == short:                                               # NoTorrent: hoster = indexer
        hm = re.search(r'([A-Za-z0-9]+)\s*-\s*Notorrent', f['text'])
        if hm: idx = hm.group(1)
    return INDEXER_MAP.get(idx.upper().replace(' ',''), idx)

def build_display(f, short):
    """Campos parseados → (name, title) en el formato compacto de 2+2 líneas."""
    idx    = _indexer_of(f, short)
    name1  = '☁️ ' + ' · '.join(x for x in [f['disp'], f['hdr']] if x)
    name2  = '🔊 ' + ' · '.join(x for x in [f['lang'], f['audio']] if x) if (f['lang'] or f['audio']) else ''
    t1 = []
    if f['cached']:            t1.append('⚡ En caché')
    elif f['seeds'] is None:   t1.append('🔗 directo')
    else:                      t1.append(f'👤 {f["seeds"]}')
    if f['size']:              t1.append(f'💾 {f["size"]:.1f} GB')
    if f['codec']:             t1.append(f'🎞 {f["codec"]}')
    t2 = [f'⚙️ {short}/{idx}' if idx.lower() != short.lower() else f'⚙️ {short}']
    if f['srctype']:           t2.append(f['srctype'])
    if f['es']:                t2.append('ES')
    if f['dur']:               t2.append(f'⏱ {f["dur"]}')
    return '\n'.join(x for x in [name1, name2] if x), '\n'.join(' · '.join(x) for x in [t1, t2] if x)

def sanitize_stream(s):
    """Copia del stream con tipos válidos para stremio-web (un fileIdx:null rompe todo)."""
    out = {k: v for k, v in dict(s).items() if v is not None}
    if 'fileIdx' in out and (isinstance(out['fileIdx'], bool) or not isinstance(out['fileIdx'], int)):
        try: out['fileIdx'] = int(out['fileIdx'])      # bool es int en py: fileIdx:true también se corrige
        except (TypeError, ValueError): out.pop('fileIdx')
    if 'infoHash' in out and not isinstance(out['infoHash'], str): out.pop('infoHash')
    if 'infoHash' in out:
        out['infoHash'] = out['infoHash'].lower()
        out.setdefault('fileIdx', 0)          # como Torrentio: siempre idx
    for k in ('url', 'externalUrl', 'ytId'):
        if k in out and not isinstance(out[k], str): out.pop(k)
    if 'sources' in out and not isinstance(out['sources'], list): out.pop('sources')
    if 'subtitles' in out and not isinstance(out['subtitles'], list): out.pop('subtitles')
    if 'behaviorHints' in out and not isinstance(out['behaviorHints'], dict): out.pop('behaviorHints')
    return out

def norm_stream(s, short):
    """Pipeline: parsea → sanitiza → formatea. Devuelve (stream, res, seeds, size, cached)."""
    f = extract_fields(s)
    out = sanitize_stream(s)
    out['name'], out['title'] = build_display(f, short)
    seeds = (f['seeds'] or 0) + (500 if f['es'] else 0)   # ES/LATAM sube dentro de su resolución
    return out, f['res'], seeds, f['size'] or 0, f['cached']

RANK = {'4320P': 9, '2160P': 8, '1440P': 7, '1080P': 6, 'HD': 6, '720P': 5, '576P': 4, '480P': 3, '360P': 2, '240P': 1, 'SD': 1}

_cache    = {}            # (stype,sid) -> (ts, [streams])
_meta_c   = {}            # meta cacheada
_cat_c    = {}            # catálogo cacheado
_inflight = {}            # (stype,sid) -> estado compartido
_slock    = threading.Lock()
HEALTH    = {}            # fuente -> {'ok':n,'fail':n,'last':ts,'err':str,'n':usable,'streak':n}
_fails    = {}            # host -> nº fetchs fallados (observabilidad en /health)
_rtime    = {}            # ruta -> (n, total_ms, max_ms) — latencia del servidor
_rts      = {}            # ruta -> deque(ultimas 512 latencias) — histograma p50/p95/p99

logging.basicConfig(level=logging.INFO, format='%(message)s')
_log = logging.getLogger('stremio-proxy')

def log_ev(ev, **kv):
    """Un evento = una línea JSON — grep/journalctl -o json-pretty friendly."""
    _log.info(json.dumps({'ev': ev, **kv}, separators=(',', ':')))

def _pct(vals, q):
    if not vals: return 0
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * q))], 1)
COOLDOWN  = {}            # fuente -> (ts, motivo) hasta el que se salta
COOLDOWN_T = 12*3600      # cuota agotada: 12h
FAIL_CD_T  = 15*60        # racha de fallos: 15 min y reintenta
FAIL_STREAK= 3
_RE_LIMIT  = re.compile(r'daily limit|limit reached|link limit|quota', re.I)
_RE_SPAM   = re.compile(r'daily limit|limit reached|link limit|upgrade (your|to|premium|account)|premium streams|free trial|join our discord|discord\.gg', re.I)
_SOFT      = ('cuota', 'espera', 'manifest sin stream', 'tipo no soportado', 'prefijo')

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

STALE_MAX = 24*3600   # techo del stale-if-error: upstream muerto >24h = vacío

def _cstale(cache, key):
    """Último valor aunque caducado (max 24h): mejor dato viejo que 'Failed to fetch'."""
    ent = cache.get(key)
    return ent[1] if ent and time.time() - ent[0] < STALE_MAX else None

_idmap     = {}            # (pref, base_id) -> imdb cacheado
_PREFIX_SRC = {'kitsu': 'Kitsu', 'mal': 'Kitsu', 'anilist': 'Kitsu', 'anidb': 'Kitsu',
               'tmdb': 'TMDB'}   # fuente que resuelve la meta (lleva imdb_id) por prefijo

def _resolve_sid(stype, sid):
    """kitsu:7442:1:1 -> tt2560140:1:1 — las fuentes entienden imdb, no kitsu/mal.
    Formato episodio-absoluto (kitsu:49847:2) -> mapeado a :S:E vía videos del meta."""
    pref = sid.split(':', 1)[0]
    if pref not in _PREFIX_SRC: return sid
    rest = sid.split(':', 1)[1]
    base_id = rest.split(':', 1)[0]
    ep = rest[len(base_id):]
    key = (pref, base_id)
    ent = _cget(_idmap, key)
    if ent is None:
        base = next((b.base for b in SOURCES if b.name == _PREFIX_SRC[pref]), None)
        d = fetch(f'{base}/meta/{stype}/{pref}:{base_id}.json', 4) if base else None
        m = (d or {}).get('meta') or {}
        ent = (m.get('imdb_id') or '', m.get('videos') or [])
        _cset(_idmap, key, ent)
    imdb, vs = ent
    if not imdb: return sid
    segs = ep.strip(':').split(':') if ep else []
    if len(segs) == 1 and segs[0].isdigit() and vs:               # 'kitsu:49847:2' -> ':1:2'
        n = int(segs[0])
        if 0 < n <= len(vs):
            v = vs[n - 1]
            s, e = str(v.get('season') or ''), str(v.get('episode') or '')
            if s.isdigit() and e.isdigit():
                ep = f':{int(s)}:{int(e)}'
            else:
                vsid = str(v.get('id') or '').split(':')
                if len(vsid) >= 3: ep = ':' + ':'.join(vsid[-2:])
    return imdb + ep

def _id_prefix(sid):
    """Prefijo de un id de stremio = letras/underscore iniciales.
    'tt0903747:5:1'->'tt' · 'kitsu:49847:2'->'kitsu' · 'mx:1:slug'->'mx' · '_onepace1'->'_onepace'
    (nunca split(':'): los imdb llevan el prefijo pegado a dígitos)"""
    m = re.match(r'[a-zA-Z_]+', sid or '')
    return m.group() if m else (sid or '').split(':')[0]

def _spam(s):
    raw0 = (s.get('title') or s.get('description') or '')
    rawn = s.get('name') or ''
    return raw0.lstrip().startswith(('⚠','🔒')) or rawn.lstrip().startswith(('⚠','🔒')) \
        or 'ERROR!' in raw0 or _RE_SPAM.search(raw0)

def _collect(key, state):
    stype, sid = key
    sid = _resolve_sid(stype, sid)            # kitsu/mal -> imdb para las fuentes
    def one(i):
        name, base = SOURCES[i]
        if name in _DISABLED: return i, [], 0
        m = manifests[i] if i < len(manifests) else None
        if m is not None and m.get('resources') and 'stream' not in res_of(m):
            _health(name, True, 'manifest sin stream', 0)
            return i, [], 0
        ts = res_types(m, 'stream')
        if ts is not None and stype not in ts:                        # PublicDom: solo movies
            _health(name, True, 'tipo no soportado', 0)
            return i, [], 0
        prefs = res_prefixes(m, 'stream')
        pref = _id_prefix(sid)
        if prefs is not None and pref not in prefs:                   # Torrentio solo habla tt/kitsu
            _health(name, True, 'prefijo no soportado', 0)
            return i, [], 0
        cd = COOLDOWN.get(name)
        if cd and time.time() < cd[0]:                                # cuota o fallos: saltar
            _health(name, True, f'en espera ({cd[1]})', 0)
            return i, [], 0
        d = fetch(f"{base}/stream/{stype}/{urllib.parse.quote(sid, safe=':')}.json", STREAM_TIMEOUT)
        ss = (d or {}).get('streams')
        if not isinstance(ss, list): ss = []            # "streams":null o tipo raro
        if any(isinstance(s, dict) and _RE_LIMIT.search(str(s.get('title') or '')) for s in ss):
            COOLDOWN[name] = (time.time() + COOLDOWN_T, 'cuota')       # disyuntor 12h
            _health(name, True, 'cuota agotada → espera 12h', 0)
            return i, [], 0
        usable = sum(1 for s in ss if isinstance(s, dict) and not _spam(s))
        _health(name, d is not None, '' if d is not None else 'timeout/error', usable)
        if d is None and HEALTH[name]['streak'] >= FAIL_STREAK:        # racha de fallos: descanso
            COOLDOWN[name] = (time.time() + FAIL_CD_T, 'fallos')
        return i, ss, usable
    t0 = time.time()
    try:
        for f in as_completed([_POOL.submit(one, i) for i in range(len(SOURCES))]):
                try: i, streams, usable = f.result()
                except Exception: continue
                state['contrib'][SOURCES[i].name] = usable            # por collect: sin mezclas
                with state['lock']:
                    for s in streams:
                        if not isinstance(s, dict) or _spam(s): continue
                        k = s.get('infoHash') or s.get('url') or s.get('externalUrl') or s.get('ytId')
                        if not k: continue
                        k = str(k).lower()              # infoHash: Ytztvio usa MAYÚSCULAS
                        if k in state['seen']: continue
                        state['seen'].add(k)
                        state['rows'].append(norm_stream(s, SOURCES[i].name))
    finally:
        with state['lock']:
            final = [r[0] for r in sorted(state['rows'], key=lambda r: (-RANK.get(r[1], 0), -r[4], -r[2], -r[3]))]
        with _slock:
            _cset(_cache, key, final)
            _inflight.pop(key, None)
        recent = {n: h for n, h in HEALTH.items() if time.time() - h['last'] < 8}
        fails  = [f"{n}({h['err']})" for n, h in recent.items()
                  if h['err'] and not any(s in h['err'] for s in _SOFT)]
        contribs = ' '.join(f"{n}:{u}" for n, u in
                            sorted(state['contrib'].items(), key=lambda kv: -kv[1]))
        print(f"[done] {key[1]} → {len(final)} streams · {time.time()-t0:.1f}s · [{contribs}]"
              f" · fallos: {', '.join(fails) or 'ninguno'}", flush=True)
        state['done'].set()                             # siempre: el cliente nunca espera de más

def _ensure_collect(key):
    with _slock:
        state = _inflight.get(key)
        if state is None:
            state = {'rows': [], 'seen': set(), 'contrib': {}, 'done': threading.Event(), 'lock': threading.Lock()}
            _inflight[key] = state
            threading.Thread(target=_collect, args=(key, state), daemon=True).start()
        return state

def handle_stream(stype, sid):
    if sid.startswith('m3u:'):                              # canales M3U propios
        if not _M3U: _m3u_metas()
        u = _M3U.get(sid)
        return {'streams': [{'name': 'M3U', 'title': 'Canal propio', 'url': u}] if u else []}
    key = (stype, sid)
    hit = _cget(_cache, key)
    if hit is not None:
        return {'streams': hit}
    state = _ensure_collect(key)
    state['done'].wait(STREAM_BUDGET)
    with state['lock']:
        rows = sorted(state['rows'], key=lambda r: (-RANK.get(r[1], 0), -r[4], -r[2], -r[3]))
    out = [r[0] for r in rows]
    _debrid_mark(out)                                        # si hay key: cache + resolve /r/
    _url_health(out)                                         # HEAD async a streams http
    return {'streams': out}


# ---- health-check de streams HTTP (3.4): HEAD async, muertos filtrados ----
_URL_BAD = {}        # url -> ts marcado muerto (reintento a los 30min)
_URL_OK  = {}        # url -> ts verificado vivo
def _url_health(streams):
    """Streams http: los confirmados muertos salen de la lista; los desconocidos
    se HEAD-encolan async (máx 15, 3s). Fallo transitorio (timeout) solo cuenta
    medio strike — 2 strikes o un 4xx/5xx confirman muerte."""
    now = time.time()
    dead = {u for u, (st, ts) in _URL_BAD.items() if st >= 2 and now - ts < 1800}
    if dead:
        streams[:] = [s for s in streams if s.get('url') not in dead]
    todo = [s['url'] for s in streams
            if s.get('url', '').startswith('http') and '127.0.0.1' not in s['url']
            and s['url'] not in _URL_BAD and now - _URL_OK.get(s['url'], 0) > 1800][:15]
    def head(u):
        try:
            r = _session(urllib.parse.urlsplit(u).netloc).head(u, timeout=3, allow_redirects=True)
            if r.status_code < 400:
                _URL_OK[u] = time.time(); _URL_BAD.pop(u, None)
            else:
                _URL_BAD[u] = (2, time.time())                 # 4xx/5xx: muerte segura
        except Exception:
            st, _ = _URL_BAD.get(u, (0, 0))
            _URL_BAD[u] = (st + 1, time.time())                # timeout: strike
    for u in todo:
        _POOL_BG.submit(head, u)


# ---- debrid (Real-Debrid / TorBox) — activo solo con key en config.json/env ----
_DB_OK = {}
def _db_cfg():
    p, k = CFG.get('debrid_provider'), CFG.get('debrid_key') or os.environ.get('DEBRID_KEY', '')
    return (p, k) if p and k else (None, None)

def _db_avail(hashes):
    """Hashes cacheados en el debrid -> set (lote, cacheado en _DB_OK)."""
    p, k = _db_cfg()
    miss = [h for h in hashes if h not in _DB_OK]
    if p and miss:
        try:
            if p == 'real-debrid':
                r = _session('api.real-debrid.com').get(
                    'https://api.real-debrid.com/rest/1.0/torrents/instantAvailability/' + '/'.join(miss[:40]),
                    headers={'Authorization': f'Bearer {k}'}, timeout=6)
                d = r.json()
                for h in miss[:40]:
                    e = d.get(h) or {}
                    _DB_OK[h] = bool(e.get('rd') or (e.get('files') or []))
            elif p == 'torbox':
                r = _session('api.torbox.app').get(
                    'https://api.torbox.app/v1/api/torrents/checkcached?hash=' + ','.join(miss[:40]),
                    headers={'Authorization': f'Bearer {k}'}, timeout=6)
                ok = set((r.json().get('data') or {}).keys())
                for h in miss[:40]: _DB_OK[h] = h.lower() in ok
        except Exception:
            pass
    return {h for h in hashes if _DB_OK.get(h)}

def _debrid_mark(streams):
    p, k = _db_cfg()
    if not p: return
    hashes = [s.get('infoHash') for s in streams if s.get('infoHash')]
    if not hashes: return
    ok = _db_avail(hashes)
    for s in streams:
        h = s.get('infoHash')
        if h and h in ok:
            s['cached'] = True
            s['name'] = '⚡ ' + (s.get('name') or '')
            s['url'] = f'http://127.0.0.1:{PORT}/r/{p}/{h}?fi={s.get("fileIdx", 0)}'
            s.pop('infoHash', None); s.pop('sources', None)

def _db_resolve(p, h, fi):
    """Resuelve infoHash -> URL HTTP final del debrid (redirect 302)."""
    _, k = _db_cfg()
    if not k: return None
    try:
        if p == 'real-debrid':
            h_ = {'Authorization': f'Bearer {k}'}
            s = _session('api.real-debrid.com')
            t = s.post('https://api.real-debrid.com/rest/1.0/torrents/addMagnet',
                       data={'magnet': f'magnet:?xt=urn:btih:{h}'}, headers=h_, timeout=8).json()
            tid = t['id']
            inf = s.get(f'https://api.real-debrid.com/rest/1.0/torrents/info/{tid}', headers=h_, timeout=8).json()
            fs = [f for f in inf.get('files', []) if f.get('name', '').lower().endswith(
                ('.mp4', '.mkv', '.avi', '.m4v', '.webm'))]
            fsel = fs[int(fi)] if int(fi) < len(fs) else max(fs, key=lambda f: f.get('bytes', 0))
            s.post(f'https://api.real-debrid.com/rest/1.0/torrents/selectFiles/{tid}',
                   data={'files': fsel['id']}, headers=h_, timeout=8)
            inf = s.get(f'https://api.real-debrid.com/rest/1.0/torrents/info/{tid}', headers=h_, timeout=8).json()
            link = (inf.get('links') or [None])[fs.index(fsel) if fsel in fs else 0]
            d = s.post('https://api.real-debrid.com/rest/1.0/unrestrict/link',
                       data={'link': link}, headers=h_, timeout=8).json()
            return d.get('download')
        elif p == 'torbox':
            s = _session('api.torbox.app')
            h_ = {'Authorization': f'Bearer {k}'}
            t = s.post('https://api.torbox.app/v1/api/torrents/createtorrent',
                       data={'magnet': f'magnet:?xt=urn:btih:{h}'}, headers=h_, timeout=10).json()
            tid = (t.get('data') or {}).get('torrent_id')
            lnk = s.get('https://api.torbox.app/v1/api/torrents/requestdl',
                        params={'token': k, 'torrent_id': tid, 'file_id': fi},
                        headers=h_, timeout=10).json()
            return (lnk.get('data') or {}).get('link') or (lnk.get('data'))
    except Exception:
        return None

# ---- manifest/catalgos/meta ----
_NO_CATALOG = {'Orion',    # catálogos muertos sin config (metas:[] en Discover)
               'Stravo'}   # catálogos = clones idénticos de StravoII (streams sí aporta)

# Max descriptor size de la colección remota: AllIPTV solo géneros + ES/EN/PT (no 55 idiomas)
_CAT_KEEP = {
    'AllIPTV': re.compile(
        r'^cat_(all|animation\w*|auto|business|classic|comedy|cooking|culture|documentary|'
        r'education|entertainment|family|general|interactive|kids|legislative|lifestyle|'
        r'movies|music|news|outdoor|public|relax|religious|science|series|shop|sports|'
        r'travel|weather|xxx|undefined|spanish|english|portuguese)$', re.I),
}

_PT_ES = (('Filmes', 'Películas'), ('Séries', 'Series'), ('Recentes', 'Recientes'),
          ('Em Alta', 'En Tendencia'), ('Lançamentos', 'Estrenos'), ('Novos', 'Nuevas'))

_ANIME_CAT = re.compile(r'\banim', re.I)      # catálogo declarado movie/series pero es anime
_UTYPE     = {}                                # catalog_id -> tipo real en upstream

_GRP_MIN = 2                                   # >=N catálogos de un tipo -> 1 catálogo + selector género
_GRP     = {}                                  # cid agrupado -> OrderedDict etiqueta -> (utype, upstream_id, quiere_año)
_YR_OPTS = {}                                  # cid agrupado -> [años] del 2º picker 'year'
_REQ     = set()                               # cids con extras requeridos upstream: quedan planos
_TYPE_ES = {'movie': 'Películas', 'series': 'Series', 'anime': 'Anime',
            'tv': 'TV', 'channel': 'Canales'}

_SYNTH = {}                                       # cid sintético -> (utype, real, extra por defecto)
_YEARS  = [str(y) for y in range(time.gmtime().tm_year, time.gmtime().tm_year - 12, -1)]

_EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿️\u200d]+')
def _noemoji(s):
    s2 = _EMOJI.sub('', s)
    s2 = re.sub(r'\s{2,}', ' ', s2).strip(' ··-—|:')
    return s2 or s.strip()

_RANK = {n: i for i, n in enumerate((          # orden agradable en el selector de Discover
    'Cinemeta', 'NoTorrent', 'TMDB', 'IMDBCat', 'RTomatoes', 'AniSeason', 'TorrentCat',
    'OnePace', 'StravoII', 'Stravo', 'Fenix', 'PublicDom',
    'Kitsu', 'YouTube', 'IPTV', 'AllIPTV'))}

def _cat_name(src, c):
    """'Kitsu·Kitsu' -> 'Kitsu·List' · 'MAL: X' -> 'MAL·X' · 'AllIPTV·All IPTV X' -> 'AllIPTV·X' · PT->ES."""
    n = (c.get('name') or c['id']).strip()
    flex = r'\s*'.join(map(re.escape, src))                                 # 'AllIPTV' casa 'All IPTV'
    n = re.sub(rf'^{flex}[:·\s\-.]+', '', n, flags=re.I).strip()             # prefijo redundante
    n = re.sub(r'^tmdb\.', '', n, flags=re.I).strip()                         # tmdb.top -> top
    if not n or n.lower() == src.lower():
        n = c['id'].rsplit('-', 1)[-1].replace('.', ' ').title()
        if n.lower() == src.lower(): return src
    for pt, es in _PT_ES:
        n = re.sub(rf'\b{pt}\b', es, n)
    return f'{src} · {_noemoji(n)}'

def build_manifest():
    cats = []
    seen = set()
    for i, m in enumerate(manifests):
        if not m or SOURCES[i].name in _NO_CATALOG or SOURCES[i].name in _DISABLED: continue
        keep = _CAT_KEEP.get(SOURCES[i].name)
        for c in m.get('catalogs', []):
            if keep and not keep.search(c['id']): continue
            req = list(c.get('extraRequired') or []) + \
                  [e.get('name') for e in (c.get('extra') or []) if e.get('isRequired')]
            # 'search'/'skip' requeridos -> opción muerta; lastVideosIds/calendarVideosIds
            # los aporta LA APP desde tu biblioteca (Continue Watching / Calendario)
            if any(r not in ('genre', 'lastVideosIds', 'calendarVideosIds') for r in req):
                continue
            nc = dict(c)
            cid = f"s{SOURCES[i].name.lower()}_{c['type']}_{c['id']}"  # tipo upstream en slug: único
            if cid in seen: continue                         # upstream repite el mismo catálogo
            seen.add(cid)
            nc['id'] = cid
            nc['name'] = _cat_name(SOURCES[i].name, c)
            if req: _REQ.add(cid)                            # su selector nativo importa: no agrupar
            if nc.get('extraRequired'): del nc['extraRequired']  # 'genre' opcional: grid sin obligar filtro
            _UTYPE[cid] = c['type']                            # tipo real upstream
            if c['type'] in ('movie', 'series') and _ANIME_CAT.search(f"{c.get('name', '')} {c['id']}"):
                nc['type'] = 'anime'                           # contenido anime -> Discover anime
            cats.append((_RANK.get(SOURCES[i].name, 99), nc))
    # fuentes con muchos catálogos de un tipo -> un catálogo con 2º selector (genre)
    import collections
    bytype = collections.defaultdict(list)
    for rank, nc in cats:
        src = nc['id'].split('_', 1)[0][1:]                  # 'sstravoii' -> 'stravoii'
        bytype[(src, nc['type'])].append((rank, nc))
    cats = []
    for (src, dtype), members in bytype.items():
        # miembros _REQ cuyo único extra requerido es genre=año se pliegan:
        # el grupo declara un 2º picker 'year' con las opciones reales upstream
        members_in, year_opts = [], set()
        for r, nc in members:
            if nc['id'] not in _REQ:
                members_in.append((r, nc, False)); continue
            reqs = {e.get('name') for e in (nc.get('extra') or []) if e.get('isRequired')}
            yopts = next((e.get('options') for e in nc['extra'] if e.get('name') == 'genre'), None)
            if reqs == {'genre'} and yopts and all(re.fullmatch(r'\d{4}', o) for o in yopts):
                members_in.append((r, nc, True))
                year_opts.update(yopts)
        if len(members_in) < _GRP_MIN:
            cats.extend(members)
            continue
        opts = collections.OrderedDict()
        for r, nc, wants in members_in:
            label = _noemoji(nc['name'].split(' · ', 1)[-1] or nc['id'])
            ut, _, real = nc['id'].split('_', 1)[1].partition('_')
            if label in opts: label = f"{label} ({ut.title()})"
            while label in opts: label += ' ·'
            opts[label] = (ut, real, wants)
        gid = f"s{src}_{dtype}_grp"
        _GRP[gid] = opts
        _YR_OPTS[gid] = sorted(year_opts, reverse=True)
        up_extras = {e.get('name') for r, nc, _ in members_in for e in (nc.get('extra') or [])} - {'genre'}
        extra = [{'name': 'genre', 'isRequired': False, 'options': list(opts)}]
        if year_opts: extra.append({'name': 'year', 'isRequired': False, 'options': _YR_OPTS[gid]})
        extra += [{'name': n, 'isRequired': False} for n in ('search', 'skip') if n in up_extras]
        cats.append((members[0][0], {'id': gid, 'type': dtype,
                            'name': members[0][1]['name'].split(' · ')[0],
                            'extra': extra}))
        folded = {nc['id'] for r, nc, _ in members_in}
        cats.extend((r, nc) for r, nc in members if nc['id'] not in folded)  # resto _REQ: planos
    # catálogo sintético: 'Estrenos' = cinemeta year+genre=año actual (incluye no estrenados aún)
    ecid = 'scinemeta_movie_estrenos'
    _SYNTH[ecid] = ('movie', 'year', f'genre={_YEARS[0]}')
    cats.append((_RANK['Cinemeta'], {'id': ecid, 'type': 'movie',
                 'name': 'Cinemeta · Estrenos',
                 'extra': [{'name': 'genre', 'isRequired': False, 'options': _YEARS}]}))
    # estrenos de cine EN EL CALENDARIO: metasDetailed con vídeo sintético = fecha
    # de estreno. Declarado como 'series' porque el calendario solo consulta
    # catálogos series — los metas siguen type=movie y la ficha abre bien.
    ccid = 'scinemeta_series_estrenoscal'
    _SYNTH[ccid] = ('movie', '_estrenos_cal', None)
    cats.append((_RANK['Cinemeta'], {'id': ccid, 'type': 'series',
                 'name': 'Cinemeta · Próximos estrenos',
                 'extra': [{'name': 'calendarVideosIds', 'isRequired': True}]}))
    # búsqueda global: un catálogo por tipo que hace fan-out a todas las fuentes
    for t in ('movie', 'series', 'anime'):
        gid = f'suniform_{t}_search'
        _SYNTH[gid] = (t, '_busqueda', None)
        cats.append((_RANK.get('Cinemeta', 0), {'id': gid, 'type': t,
                     'name': 'Buscar todo',
                     'extra': [{'name': 'search', 'isRequired': True}]}))
    # 'Porque viste X' — solo con cuenta Trakt conectada (/auth/trakt)
    if CFG.get('trakt_access_token'):
        for t in ('movie', 'series'):
            rid = f'suniform_{t}_recom'
            _SYNTH[rid] = (t, '_recom', None)
            cats.append((_RANK.get('Cinemeta', 0), {'id': rid, 'type': t,
                         'name': 'Recomendados · tu Trakt'}))
    # canales M3U propios (config.m3u_path)
    if CFG.get('m3u_path') and os.path.exists(CFG['m3u_path']):
        mid = 'sm3u_tv_channels'
        _SYNTH[mid] = ('tv', '_m3u', None)
        cats.append((_RANK.get('IPTV', 0), {'id': mid, 'type': 'tv',
                     'name': 'Mis canales'}))
    cats = [nc for _, nc in sorted(cats, key=lambda t: t[0])]
    counts = collections.Counter((c['name'], c['type']) for c in cats)  # dup solo choca en el mismo tipo
    taken = {}
    for nc in cats:
        n = nc['name']
        if counts[(n, nc['type'])] > 1:
            u = _UTYPE.get(nc['id'], nc['type']).title()
            k = (n, u)
            taken[k] = taken.get(k, 0) + 1
            nc['name'] = f"{n} ({u}{f' {taken[k]}' if taken[k] > 1 else ''})"
    return {
        'id': 'local.uniform.streams', 'version': '1.0.0',
        'name': 'Streams Unificados',
        'description': 'Streams de todos los addons con formato uniforme + catálogos fusionados',
        'resources': ['catalog', 'meta', 'stream'],
        'types': ['movie', 'series', 'anime', 'channel', 'tv'],
        'catalogs': cats,
        'idPrefixes': ['tt', 'kitsu', 'mal', 'anilist', 'anidb', 'tmdb', 'tvdb', 'ia', 'tpb', 'pluto', 'pd', 'yt', 'yt_id', 'iptv', 'local', 'mx', '_onepace', 'm3u'],
        'behaviorHints': {'newEpisodeNotifications': True},
    }

_LEGACY = ['Torrentio','TorrentsDB','MediaFusion','Peerflix','Meteor','Ytztvio',
           'NoTorrent','Orion','PublicDom']   # orden de ids antiguos sN_ (cacheados por la app)

def _pv(claims, prop):
    for s in claims.get(prop, []):
        v = s.get('mainsnak', {}).get('datavalue', {}).get('value')
        if v is not None: return v
    return None

_wd_c = {}                                    # yt -> (ts, dict) — TTL 24h: no cambia a diario

def _wikidata(yt):
    """Canal yt -> datos reales de Wikidata (country/website/socials/year) por P2397."""
    ent = _wd_c.get(yt)
    if ent and time.time() - ent[0] < 86400: return ent[1]
    out = {}
    d = fetch(f'https://www.wikidata.org/w/api.php?action=query&list=search'
              f'&srsearch=haswbstatement:P2397={yt}&format=json&srlimit=1', 6)
    q = ((d or {}).get('query', {}).get('search') or [{}])[0].get('title')
    if q:
        e = (fetch(f'https://www.wikidata.org/wiki/Special:EntityData/{q}.json', 8) or {}
             ).get('entities', {}).get(q, {})
        c = e.get('claims', {})
        cq = _pv(c, 'P17') or _pv(c, 'P495')                  # país / país de origen
        if isinstance(cq, dict):
            d2 = fetch(f'https://www.wikidata.org/wiki/Special:EntityData/{cq["id"]}.json', 6)
            out['country'] = ((d2 or {}).get('entities', {}).get(cq['id'], {})
                              .get('labels', {}).get('en', {}).get('value'))
        web = _pv(c, 'P856')
        if isinstance(web, str): out['website'] = web
        inc = _pv(c, 'P571')
        if isinstance(inc, dict) and inc.get('time'): out['year'] = inc['time'][1:5]
        for p, label, base in (('P2002', 'X', 'https://x.com/'),
                               ('P2003', 'Instagram', 'https://instagram.com/'),
                               ('P2013', 'Facebook', 'https://facebook.com/')):
            h = _pv(c, p)
            if isinstance(h, str): out.setdefault('social', []).append(
                {'name': label, 'category': 'social', 'url': base + h.lstrip('@')})
    _wd_c[yt] = (time.time(), out)
    return out

_YT_AWARDS = [(100e6, 'Red Diamond'), (50e6, 'Ruby'), (10e6, 'Diamond'),
              (1e6, 'Gold'), (1e5, 'Silver')]

def _fmt_num(n):
    try: n = float(n)
    except (TypeError, ValueError): return None
    for u, d in (('B', 1e9), ('M', 1e6), ('K', 1e3)):
        if n >= d: return f'{n/d:.1f}{u}'
    return str(int(n))

def _fix_metas(d, deep=False):
    """Canal yt: avatar ggpht caducado -> unavatar; descripción vacía -> stats reales;
    deep=True (detalle): + Wikidata (país, web, social, año), awards por subs, logo, vídeos muertos."""
    ms = d.get('metas') or ([d['meta']] if isinstance(d.get('meta'), dict) else [])
    for m in ms:
        yt = m.get('yt_id')
        i = str(m.get('id') or '')
        if not yt and i.startswith('yt_id:'): yt = i[6:]
        if not yt: continue
        p = m.get('poster') or ''
        if 'ggpht.com' in p or 'googleusercontent.com' in p:
            m['poster'] = f'https://unavatar.io/youtube/{yt}'
        if not (m.get('description') or '').strip():
            parts = []
            subs, vc = _fmt_num(m.get('popularity')), _fmt_num(m.get('viewCount'))
            if subs: parts.append(f'{subs} suscriptores')
            if vc:   parts.append(f'{vc} reproducciones')
            g = m.get('genre')
            if g:    parts.append(' · '.join(g[:3]) if isinstance(g, list) else str(g))
            m['description'] = 'Canal de YouTube' + (' · ' + ' · '.join(parts) if parts else '')
        if not (m.get('keywords') or '').strip() and m.get('name'):
            m['keywords'] = m['name']
        if not m.get('logo') and m.get('poster'):
            m['logo'] = m['poster']                                   # canal: logo ≈ avatar
        try: subs_n = int(float(m.get('popularity') or 0))
        except (TypeError, ValueError): subs_n = 0
        if not m.get('awards'):
            aw = next((f'YouTube {n} Creator Award' for t, n in _YT_AWARDS if subs_n >= t), None)
            if aw: m['awards'] = aw                                   # premios reales por subs
        if deep:
            vids = m.get('videos')
            fw = _POOL.submit(_wikidata, yt)
            fd = _POOL.submit(_dead_videos, vids) if vids else None   # en paralelo con wikidata
            wd = fw.result()
            if wd.get('country') and not m.get('country'): m['country'] = wd['country']
            if wd.get('year') and not m.get('releaseInfo'): m['releaseInfo'] = wd['year']
            if not m.get('links'):
                links = ([{'name': 'Web oficial', 'category': 'official', 'url': wd['website']}]
                         if wd.get('website') else []) + wd.get('social', [])
                if links: m['links'] = links
            if vids:
                first = str(vids[0].get('id') or '').rsplit(':', 1)[-1]
                if first:
                    if not m.get('background'):                # backdrop = thumb del último vídeo
                        m['background'] = f'https://i.ytimg.com/vi/{first}/hqdefault.jpg'
                    if not m.get('unsubscribedTrailer'):       # trailer = último vídeo
                        m['unsubscribedTrailer'] = first
                dead = fd.result()
                if dead: m['videos'] = [v for v in vids if v.get('id') not in dead]
    return d

def _dead_videos(vids):
    """Vídeos borrados/privados: todos sus thumbs 404 -> fuera del meta (fichas rotas)."""
    def chk(v):
        vid = str(v.get('id') or '').rsplit(':', 1)[-1]
        if not vid: return None
        try:
            r = _session('i.ytimg.com').head(f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg', timeout=4)
            return v.get('id') if r.status_code != 200 else None
        except Exception: return None
    fs = [_POOL.submit(chk, v) for v in vids[:40]]               # primera página visible
    return {f.result() for f in fs if f.result()}

def _anilist_airing(days):
    """Próximas emisiones de anime (AniList GraphQL, sin auth):
    [{ep, airingAt, idMal, title, img}] ordenadas por fecha."""
    out, seen = [], set()
    t_end = int(time.time()) + days * 86400
    for page in (1, 2, 3):
        q = {'query': '{Page(page:%d,perPage:50){airingSchedules(notYetAired:true,'
                     'airingAt_lesser:%d,sort:TIME){airingAt episode media{idMal '
                     'title{romaji english} coverImage{large}}}}}' % (page, t_end)}
        try:
            r = _session('graphql.anilist.co').post('https://graphql.anilist.co',
                json=q, timeout=8).json()
        except Exception:
            break
        rows = ((r.get('data') or {}).get('Page') or {}).get('airingSchedules') or []
        if not rows: break
        for a in rows:
            m = a.get('media') or {}
            key = (m.get('idMal'), a.get('episode'))
            if not m.get('idMal') or key in seen: continue
            seen.add(key)
            out.append({'ep': a.get('episode'), 'airingAt': a.get('airingAt'),
                        'idMal': m['idMal'], 'img': (m.get('coverImage') or {}).get('large'),
                        'title': (m.get('title') or {}).get('english')
                                 or (m.get('title') or {}).get('romaji')})
    return out


def _trakt_movies(days):
    """Estrenos de cine con fecha real via Trakt (client_id público, gratis).
    Sin CFG['trakt_client_id'] devuelve []."""
    cid = CFG.get('trakt_client_id')
    if not cid: return []
    try:
        r = _session('api.trakt.tv').get(
            f'https://api.trakt.tv/calendars/all/movies/{time.strftime("%Y-%m-%d")}/{days}',
            headers={'trakt-api-version': '2', 'trakt-api-key': cid}, timeout=8)
        out = []
        for e in r.json():
            mv = e.get('movie') or {}
            ids = mv.get('ids') or {}
            if not ids.get('imdb'): continue
            out.append({'id': 'tt' + str(ids['imdb']).lstrip('t'),
                        'imdb_id': 'tt' + str(ids['imdb']).lstrip('t'),
                        'type': 'movie', 'name': mv.get('title'),
                        'released': e.get('released') + 'T00:00:00.000Z',
                        'year': mv.get('year'),
                        'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img"})
        return out
    except Exception:
        return []

# ---- Trakt OAuth (device flow) — token personal para /calendars/my/* ----
_TRAKT_DEV = {}                    # device_code/interval/expires mientras autoriza
def _trakt_h(auth=False):
    h = {'trakt-api-version': '2', 'trakt-api-key': CFG.get('trakt_client_id', ''),
         'Content-Type': 'application/json'}
    if auth and CFG.get('trakt_access_token'):
        h['Authorization'] = f"Bearer {CFG['trakt_access_token']}"
    return h

def _trakt_device_start():
    """POST /oauth/device/code -> user_code+verification_url para autorizar una vez."""
    try:
        d = _session('api.trakt.tv').post('https://api.trakt.tv/oauth/device/code',
            json={'client_id': CFG['trakt_client_id']}, timeout=8).json()
        _TRAKT_DEV.clear(); _TRAKT_DEV.update(d)
        _TRAKT_DEV['deadline'] = time.time() + d.get('expires_in', 600)
        threading.Thread(target=_trakt_device_poll, daemon=True).start()
        return d
    except Exception:
        return None

def _trakt_device_poll():
    """Poll /oauth/device/token hasta que el usuario autoriza (o expira)."""
    while time.time() < _TRAKT_DEV.get('deadline', 0):
        time.sleep(max(3, _TRAKT_DEV.get('interval', 5)))
        try:
            r = _session('api.trakt.tv').post('https://api.trakt.tv/oauth/device/token',
                json={'code': _TRAKT_DEV['device_code'],
                      'client_id': CFG['trakt_client_id'],
                      'client_secret': CFG['trakt_client_secret']}, timeout=8)
            d = r.json()
            if d.get('access_token'):
                CFG['trakt_access_token']  = d['access_token']
                CFG['trakt_refresh_token'] = d.get('refresh_token', '')
                CFG['trakt_expires'] = time.time() + d.get('expires_in', 7776000)
                _save_cfg(); _TRAKT_DEV.clear()
                log_ev('trakt_auth', ok=True)
                return
        except Exception:
            pass
    _TRAKT_DEV.clear()

def _trakt_refresh():
    """Renueva el access_token si caduca en <10min (grant refresh_token)."""
    if not CFG.get('trakt_refresh_token'): return
    if time.time() < CFG.get('trakt_expires', 0) - 600: return
    try:
        d = _session('api.trakt.tv').post('https://api.trakt.tv/oauth/token',
            json={'refresh_token': CFG['trakt_refresh_token'],
                  'client_id': CFG['trakt_client_id'],
                  'client_secret': CFG['trakt_client_secret'],
                  'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
                  'grant_type': 'refresh_token'}, timeout=8).json()
        if d.get('access_token'):
            CFG['trakt_access_token']  = d['access_token']
            CFG['trakt_refresh_token'] = d.get('refresh_token', CFG['trakt_refresh_token'])
            CFG['trakt_expires'] = time.time() + d.get('expires_in', 7776000)
            _save_cfg()
    except Exception:
        pass

def _trakt_my(days):
    """Calendario PERSONAL: shows + movies del usuario (requiere token OAuth).
    -> (eps[(tt, season, ep, when, show_meta)], films[{meta-like}])."""
    if not CFG.get('trakt_access_token'): return [], []
    _trakt_refresh()
    today = time.strftime('%Y-%m-%d')
    s = _session('api.trakt.tv'); h = _trakt_h(auth=True)
    eps, films = [], []
    try:
        for e in s.get(f'https://api.trakt.tv/calendars/my/shows/{today}/{days}',
                       headers=h, timeout=8).json():
            sh = e.get('show') or {}; ids = sh.get('ids') or {}
            ep = e.get('episode') or {}
            if not ids.get('imdb'): continue
            eps.append(('tt' + str(ids['imdb']).lstrip('t'), ep.get('season', 1),
                        ep.get('number', 1), e.get('first_aired'),
                        {'id': 'tt' + str(ids['imdb']).lstrip('t'), 'type': 'series',
                         'name': sh.get('title'), 'year': sh.get('year'),
                         'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img",
                         '_my': True}))
    except Exception:
        pass
    try:
        for e in s.get(f'https://api.trakt.tv/calendars/my/movies/{today}/{days}',
                       headers=h, timeout=8).json():
            mv = e.get('movie') or {}; ids = mv.get('ids') or {}
            if not ids.get('imdb'): continue
            tt = 'tt' + str(ids['imdb']).lstrip('t')
            films.append({'id': tt, 'imdb_id': tt, 'type': 'movie', 'name': mv.get('title'),
                          'released': (e.get('released') or '')[:10] + 'T00:00:00.000Z',
                          'year': mv.get('year'),
                          'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img"})
    except Exception:
        pass
    return eps, films

def _recom(utype):
    """'Porque viste X' (4.5): historial reciente Trakt -> /related por item,
    dedup por imdb. Solo con token OAuth."""
    if not CFG.get('trakt_access_token'): return {'metas': []}
    _trakt_refresh()
    kind = 'movies' if utype == 'movie' else 'shows'
    s = _session('api.trakt.tv'); h = _trakt_h(auth=True)
    try:
        hist = s.get(f'https://api.trakt.tv/users/me/history/{kind}?limit=6',
                     headers=h, timeout=8).json()
    except Exception:
        return {'metas': []}
    seeds = [str((e.get('movie') or e.get('show') or {}).get('ids', {}).get('trakt', ''))
             for e in hist]
    seeds = [x for x in seeds if x and x != 'None']
    def related(slug):
        try:
            return s.get(f'https://api.trakt.tv/{kind}/{slug}/related?limit=10',
                         headers=h, timeout=8).json() or []
        except Exception:
            return []
    seen, out = set(), []
    for lst in _POOL_BG.map(related, seeds):
        for it in lst:
            ids = it.get('ids') or {}
            if not ids.get('imdb') or ids['imdb'] in seen: continue
            seen.add(ids['imdb'])
            out.append({'id': 'tt' + str(ids['imdb']).lstrip('t'),
                        'type': utype, 'name': it.get('title'), 'year': it.get('year'),
                        'poster': f"https://images.metahub.space/poster/medium/{ids['imdb']}/img"})
            if len(out) >= 40: break
        if len(out) >= 40: break
    return {'metas': out}

def _busqueda(utype, extra):
    """Búsqueda global: fan-out a TODOS los catálogos del tipo que soportan
    'search' upstream y dedup por id canónico (imdb si resoluble)."""
    kv = dict(urllib.parse.parse_qsl(extra or ''))
    q = kv.get('search')
    if not q: return {'metas': []}
    t = 'anime' if utype == 'anime' else utype
    targets = []                                   # (base, utype_up, catid)
    for i, m in enumerate(manifests):
        if not m or SOURCES[i].name in _DISABLED: continue
        for c in m.get('catalogs') or []:
            ct = _UTYPE.get(f"s{SOURCES[i].name.lower()}_{c['type']}_{c['id']}", c['type'])
            if ct != t and c['type'] != t: continue
            extras = [e.get('name') for e in (c.get('extra') or [])]
            if 'search' in extras or 'search' in (c.get('extraSupported') or []):
                targets.append((SOURCES[i].base, c['type'], c['id']))
    seen, out = set(), []
    e = urllib.parse.urlencode({'search': q})
    for d in _POOL_BG.map(lambda x: fetch(f"{x[0]}/catalog/{x[1]}/{x[2]}/{e}.json", 8), targets):
        for m in (d or {}).get('metas') or []:
            if not m or m.get('id') in seen: continue
            seen.add(m['id']); out.append(m)
    return {'metas': out}


_M3U = {}
def _m3u_metas():
    """Parsea config.m3u_path -> metas tv + streams indexados (m3u:N)."""
    global _M3U
    try:
        txt = open(CFG['m3u_path'], encoding='utf-8', errors='replace').read()
    except Exception:
        return []
    metas, _M3U = [], {}
    name = logo = group = ''
    n = 0
    for ln in txt.splitlines():
        ln = ln.strip()
        if ln.startswith('#EXTINF'):
            name = ln.rsplit(',', 1)[-1].strip() or 'Canal'
            lm = re.search(r'tvg-logo="([^"]*)"', ln); logo = lm.group(1) if lm else ''
            gm = re.search(r'group-title="([^"]*)"', ln); group = gm.group(1) if gm else ''
        elif ln and not ln.startswith('#'):
            n += 1
            cid = f'm3u:{n}'
            _M3U[cid] = ln
            metas.append({'id': cid, 'type': 'tv', 'name': name,
                          'poster': logo or 'https://i.imgur.com/tTCuczU.png',
                          'posterShape': 'square',
                          'description': group})
            name = logo = group = ''
    return metas

def _estrenos_cal(extra=None):
    """Calendario completo = series populares en emisión (episodios reales vía
    cinemeta calendar-videos con ids sembrados del top) + pelis del año no
    estrenadas (vídeo sintético = fecha de estreno)."""
    cine = next(b for n, b in SOURCES if n == 'Cinemeta')
    tmdb = next(b for n, b in SOURCES if n == 'TMDB')
    hoy, seen, out = time.strftime('%Y-%m-%d'), set(), []
    def add_film(m):
        if not m: return
        if m.get('imdb_id'): m['id'] = m['imdb_id']              # canonical tt: dedup + ficha
        rel = (m.get('released') or '')[:10]
        if not rel or rel <= hoy or m['id'] in seen: return
        seen.add(m['id'])
        m = dict(m); m['type'] = 'series'   # el pipeline del calendario solo
        m['videos'] = [{'id': f"{m['id']}:1:1", 'name': 'Estreno en cines',
                        'season': 1, 'episode': 1, 'number': 1,
                        'released': m['released'], 'firstAired': m['released'],
                        'thumbnail': m.get('poster'), 'overview': m.get('description') or ''}]
        out.append(m)
    # series: top cinemeta + anime de temporada (AniSeason, ya tt) + long-runners
    # (kitsu airing, resueltos a tt) -> calendar-videos da TODOS sus episodios
    # fechados. Se regenera solo cada petición: temporada nueva = ids nuevos.
    kitsu = next(b for n, b in SOURCES if n == 'Kitsu')
    anise = next(b for n, b in SOURCES if n == 'AniSeason')
    fs_top = _POOL_BG.submit(fetch, f"{cine}/catalog/series/top.json", 10)
    fs_air = _POOL_BG.submit(fetch, f"{kitsu}/catalog/anime/kitsu-anime-airing.json", 10)
    fs_sea = _POOL_BG.submit(fetch, f"{anise}/catalog/series/latest_anime_seasons.json", 12)
    fs_amv = _POOL_BG.submit(fetch, f"{anise}/catalog/movie/latest_anime_seasons.json", 12)
    fs = [_POOL_BG.submit(fetch, f"{cine}/catalog/movie/year/genre={y}"
                       f"{'&skip='+str(s) if s else ''}.json", 10)
          for y in (_YEARS[0], str(int(_YEARS[0]) + 1)) for s in (0, 50, 100)]
    td = fetch(f"{tmdb}/catalog/movie/tmdb.year/genre={_YEARS[0]}.json", 10) or {}
    top_ids = [m['id'] for m in (fs_top.result() or {}).get('metas') or [] if str(m.get('id','')).startswith('tt')][:30]
    sea_ids = [m['id'] for m in (fs_sea.result() or {}).get('metas') or []
               if str(m.get('id','')).startswith('tt')]
    kids = [m['id'] for m in (fs_air.result() or {}).get('metas') or []][:20]
    top_ids += [i for i in sea_ids if i not in top_ids]
    top_ids += [r for r in _POOL_BG.map(lambda i: _resolve_sid('series', i), kids)
                if r.startswith('tt') and r not in top_ids]
    if top_ids:
        cv = fetch(f"{cine}/catalog/series/calendar-videos/calendarVideosIds={','.join(top_ids)}.json", 12) or {}
        for m in cv.get('metasDetailed') or []:
            if m and m['id'] not in seen and (m.get('videos') or []):
                seen.add(m['id']); out.append(m)
    for f in fs:
        for m in (f.result() or {}).get('metas') or []: add_film(m)
    fs2 = [_POOL_BG.submit(fetch, f"{tmdb}/meta/movie/{urllib.parse.quote(m['id'], safe='')}.json", 10)
           for m in td.get('metas') or []]
    for f in fs2: add_film((f.result() or {}).get('meta'))
    # películas anime de la temporada (AniSeason) + pelis cinemeta sin fecha:
    # TMDB resuelve tt->meta con 'released' de estreno
    amv_ids = [m['id'] for m in (fs_amv.result() or {}).get('metas') or []
               if str(m.get('id','')).startswith('tt')]
    bare = [m for f in fs for m in (f.result() or {}).get('metas') or []
            if not (m.get('released') or '')[:10] and m.get('id','').startswith('tt')][:40]
    fs3 = [_POOL_BG.submit(fetch, f"{tmdb}/meta/movie/{urllib.parse.quote(i, safe='')}.json", 10)
           for i in amv_ids + [m['id'] for m in bare]]
    for f in fs3:
        m = (f.result() or {}).get('meta')
        if m and m.get('imdb_id'): m['id'] = m['imdb_id']
        add_film(m)
    # anime con fecha REAL de emisión via AniList (gratis, sin key):
    # AiringSchedule próximos N días -> episodios exactos, resueltos mal->tt
    days = CFG.get('calendar_months_ahead', 2) * 31
    al = _anilist_airing(days)
    an_ids = [a['idMal'] for a in al if a.get('idMal')]
    tts = list(_POOL_BG.map(lambda i: _resolve_sid('series', f'mal:{i}'), an_ids))
    eps_by_tt = {}
    for a, tt in zip(al, tts):
        if str(tt).startswith('tt'):
            eps_by_tt.setdefault(tt, []).append(a)
    metas_tt = {m['id']: m for m in out}
    fmeta = [_POOL_BG.submit(fetch, f"{cine}/meta/series/{i}.json", 10) for i in eps_by_tt]
    for (tt, eps), f in zip(eps_by_tt.items(), fmeta):
        m = metas_tt.get(tt) or (f.result() or {}).get('meta')
        if not m: continue
        if m['id'] not in seen:
            m = dict(m); m.setdefault('videos', [])
            seen.add(m['id']); out.append(m); metas_tt[tt] = m
        have = {v['id'] for v in metas_tt[tt].get('videos', [])}
        for a in eps:
            vid = f"{tt}:1:{a['ep']}"
            if vid in have: continue
            metas_tt[tt]['videos'].append({
                'id': vid, 'name': a.get('title') or f"Episodio {a['ep']}",
                'season': 1, 'episode': a['ep'], 'number': a['ep'],
                'released': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(a['airingAt'])),
                'firstAired': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(a['airingAt'])),
                'thumbnail': a.get('img') or m.get('poster')})
    # estrenos reales via Trakt (requiere client_id gratis en config)
    for mv in _trakt_movies(days):
        add_film(mv)
    # calendario PERSONAL Trakt (token OAuth via /auth/trakt): episodios de
    # series seguidas + pelis de la watchlist — imdb directo, sin resolver
    my_eps, my_films = _trakt_my(days)
    for mv in my_films:
        add_film(mv)
    metas_tt = {m['id']: m for m in out}                    # reindexed tras add_film
    new_tt = sorted({tt for tt, *_ in my_eps if tt not in metas_tt})
    fmy = [_POOL_BG.submit(fetch, f"{cine}/meta/series/{i}.json", 10) for i in new_tt]
    fallback = {tt: sm for tt, _, _, _, sm in my_eps}
    for tt, f in zip(new_tt, fmy):
        m = (f.result() or {}).get('meta') or fallback.get(tt)
        if not m or not m.get('id'): continue
        m = dict(m); m.setdefault('videos', []); m['id'] = tt
        metas_tt[tt] = m; out.append(m); seen.add(tt)
    for tt, s_no, e_no, when, _ in my_eps:
        m = metas_tt.get(tt)
        if not m: continue
        vid = f"{tt}:{s_no}:{e_no}"
        if vid not in {v['id'] for v in m.get('videos', [])}:
            m['videos'].append({'id': vid, 'name': f"S{s_no}E{e_no} · en tu Trakt",
                                'season': s_no, 'episode': e_no, 'number': e_no,
                                'released': when, 'firstAired': when,
                                'thumbnail': m.get('poster')})
    # SOLO metasDetailed: con 'metas' presente el cliente parsea la variante
    # ligera (sin videos) y el calendario no tiene nada que pintar
    return {'metasDetailed': out} if out else None

def handle_catalog(stype, cid, extra):
    key = (stype, cid, extra)
    hit = _cget(_cat_c, key)
    if hit is not None: return hit
    try:
        slug, rest = cid.split('_', 1)
        s = slug[1:]
        name = _LEGACY[int(s)] if s.isdigit() else s          # s6_ viejo → NoTorrent por nombre
        try:
            base = next(b for n, b in SOURCES if n.lower() == name.lower())
        except StopIteration:
            if cid not in _SYNTH: raise                       # slug desconocido y no sintético
            base = None                                       # sintético sin fuente (Buscar, M3U)
        utype, _, real = rest.partition('_')
        if utype not in {'movie', 'series', 'anime', 'channel', 'tv'}:
            real, utype = rest, _UTYPE.get(cid, stype)        # id antiguo sin tipo embebido
        if cid in _GRP:                                       # agrupado: genre -> catálogo upstream
            kv = dict(urllib.parse.parse_qsl(extra or ''))
            g = kv.pop('genre', None)
            y = kv.pop('year', None)                          # picker año (members con genre=año)
            utype, real, wants = _GRP[cid].get(g) or next(iter(_GRP[cid].values()))
            if wants: kv['genre'] = y or _YR_OPTS[cid][0]     # sin año: el más reciente
            extra = urllib.parse.urlencode(kv, safe=',')
        elif cid in _SYNTH:                                   # sintético: ruta upstream predefinida
            utype, real, defx = _SYNTH[cid]
            if real == '_busqueda':
                return _busqueda(utype, extra)
            if real == '_recom':
                hit2 = _cget(_cat_c, ('recom', utype))         # related es caro: cache normal
                if hit2 is None:
                    hit2 = _recom(utype); _cset(_cat_c, ('recom', utype), hit2)
                return hit2
            if real == '_m3u':
                return {'metas': _m3u_metas()}
            if real == '_estrenos_cal':
                hit2 = _estrenos_cal(extra)                   # extra=ids pedidos (test filtro)
                if hit2 is not None: _cset(_cat_c, key, hit2)
                return hit2 or {'metas': []}
            if not extra: extra = defx
        if extra:                                             # ids no-tt -> tt p/ Cinemeta (calendario/biblioteca)
            kv = dict(urllib.parse.parse_qsl(extra))
            for k in ('lastVideosIds', 'calendarVideosIds'):
                if k in kv:
                    ids = kv[k].split(',')[:100]          # optionsLimit:100 — fan-out acotado
                    kv[k] = ','.join(_POOL.map(lambda i: _resolve_sid(utype, i.strip()), ids))
            extra = urllib.parse.urlencode(kv, safe=',')    # comas literales: sin ellas upstream ve 1 id mutilado
    except (ValueError, StopIteration, IndexError):
        return {'metas': []}
    tail = f"/{urllib.parse.quote(extra, safe='=&,:')}" if extra else ''
    d = fetch(f"{base}/catalog/{utype}/{urllib.parse.quote(real, safe='')}{tail}.json", 7)
    if d:
        _fix_metas(d)
        _cset(_cat_c, key, d)
        return d
    return _cstale(_cat_c, key) or {'metas': []}   # upstream caído: sirve stale

def handle_meta(stype, mid):
    key = (stype, mid)
    hit = _cget(_meta_c, key)
    if hit is not None: return hit
    # prefetch: en pelis el stream-id coincide con el meta-id — al abrir la
    # ficha ya se recolecta en background y el stream sale al instante
    if stype == 'movie' and ':' not in mid:
        _ensure_collect(key)
    # estrenos del calendario llegan como 'series' aunque sean pelis: fallback
    for want in ({stype, 'movie'} if stype == 'series' else {stype}):
        def one(i):
            m = manifests[i] if i < len(manifests) else None
            if m is not None and m.get('resources') and 'meta' not in res_of(m): return None
            ts = res_types(m, 'meta')
            if ts is not None and want not in ts: return None
            d = fetch(f"{SOURCES[i].base}/meta/{want}/{urllib.parse.quote(mid, safe=':')}.json", 4)
            return d if d and d.get('meta') else None
        futs = [_POOL.submit(one, i) for i in range(len(SOURCES))]
        try:
            for f in as_completed(futs, timeout=4.5):
                if (d := f.result()) is not None:
                    _fix_metas(d, deep=True)
                    _cset(_meta_c, key, d)
                    return d
        except FutureTimeout:
            pass
    return _cstale(_meta_c, key) or {'meta': {}}

# ---- servidor ----
class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'   # keep-alive: menos TCP por petición
    timeout = 30                    # cliente que conecta y calla no retiene el hilo
    def log_message(self, fmt, *a):
        ua = self.headers.get('User-Agent', '-') if hasattr(self, 'headers') else '-'
        log_ev('req', msg=fmt % a, ua=ua[:60])
    def reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        gz = len(body) > 1024 and 'gzip' in self.headers.get('Accept-Encoding', '')
        if gz: body = gzip.compress(body)
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        if gz: self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                              # Stremio cortó la espera — sin ruido
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Content-Length', '0')
        self.end_headers()
    def do_GET(self):
        p = urllib.parse.unquote(self.path.split('?', 1)[0])           # sin query string
        if p == '/manifest.json':
            threading.Thread(target=_prefetch_catalogs, daemon=True).start()   # Discover pega caché
            return self.reply(build_manifest())
        if p == '/health':
            now = time.time()
            return self.reply({'sources': {n: dict(h, cd_min=round((COOLDOWN[n][0]-now)/60), cd_motivo=COOLDOWN[n][1])
                               if n in COOLDOWN and COOLDOWN[n][0] > now else dict(h)
                               for n, h in HEALTH.items()},
                               'fetch_fails': _fails,
                               'latency_ms': {k: {'n': n, 'avg': round(t/n, 1), 'max': round(x, 1),
                                                  'p50': _pct(list(_rts.get(k, ())), .5),
                                                  'p95': _pct(list(_rts.get(k, ())), .95),
                                                  'p99': _pct(list(_rts.get(k, ())), .99)}
                                              for k, (n, t, x) in _rtime.items()}})
        if p == '/admin':
            return self._html(_admin_page())
        if p == '/configure':
            return self._html(_configure_page())
        if p == '/auth/trakt':
            return self._html(_trakt_auth_page())
        if p == '/auth/trakt/status':
            if CFG.get('trakt_access_token') and not _TRAKT_DEV:
                return self.reply({'state': 'ok'})
            return self.reply({'state': 'pending' if _TRAKT_DEV else 'none'})
        if p.startswith('/r/'):                                        # debrid redirect
            _, _, pvd, h = p.split('/')
            fi = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('fi', ['0'])[0]
            u = _db_resolve(pvd, h, fi)
            self.send_response(302 if u else 404)
            if u: self.send_header('Location', u)
            self.send_header('Content-Length', '0'); self.end_headers()
            return
        t0 = time.time(); seg = p.split('/')[1] if '/' in p else 'root'
        try:
            for rx, h in ROUTES:
                m = rx.match(p)
                if m: return self.reply(h(m))
            return self.reply({'err': 'not found'}, 404)
        finally:
            ms = (time.time() - t0) * 1000
            n, tot, mx = _rtime.get(seg, (0, 0, 0))
            _rtime[seg] = (n + 1, tot + ms, max(mx, ms))   # latencia por ruta en /health
            _rts.setdefault(seg, deque(maxlen=512)).append(ms)
    def _html(self, body):
        b = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        p = self.path.split('?', 1)[0]
        if p == '/reload':
            load_config()
            _cache.clear(); _meta_c.clear(); _cat_c.clear()
            threading.Thread(target=refresh_manifests, daemon=True).start()
            log_ev('reload', cfg=dict(CFG))
            return self.reply({'ok': True, 'config': {k: ('***' if 'key' in k else v)
                                                      for k, v in CFG.items()}})
        if p == '/configure':
            ln = int(self.headers.get('Content-Length') or 0)
            form = urllib.parse.parse_qs(self.rfile.read(min(ln, 65536)).decode())
            cfg = dict(CFG)
            cfg['trakt_client_id'] = form.get('trakt_client_id', [''])[0].strip()
            if form.get('trakt_client_secret', [''])[0].strip():
                cfg['trakt_client_secret'] = form['trakt_client_secret'][0].strip()
            cfg['debrid_provider'] = form.get('debrid_provider', [''])[0].strip()
            if form.get('debrid_key', [''])[0].strip():
                cfg['debrid_key'] = form['debrid_key'][0].strip()
            cfg['m3u_path'] = form.get('m3u_path', [''])[0].strip()
            cfg['horizon_days'] = int(form.get('horizon_days', [CFG['horizon_days']])[0] or CFG['horizon_days'])
            cfg['stream_langs'] = [s.strip() for s in form.get('stream_langs', [''])[0].split(',') if s.strip()] or CFG['stream_langs']
            enabled = set(form.get('src', []))
            cfg['sources_disabled'] = [s.name for s in SOURCES if s.name not in enabled]
            CFG.clear(); CFG.update(cfg); _save_cfg()
            load_config()
            self.send_response(303)
            self.send_header('Location', '/configure')
            self.send_header('Content-Length', '0'); self.end_headers()
            return
        self.reply({'err': 'not found'}, 404)

_PAGE_CSS = ('<style>body{font:15px/1.5 system-ui;background:#0f0f14;color:#e8e8f0;max-width:880px;margin:24px auto;padding:0 16px}'
             'h1{font-size:22px}h2{font-size:16px;color:#9d9db5;margin-top:28px}'
             'table{border-collapse:collapse;width:100%}td,th{padding:6px 10px;border-bottom:1px solid #26262f;text-align:left}'
             'input,select{background:#1a1a24;border:1px solid #333;color:#e8e8f0;padding:6px 8px;border-radius:6px;width:100%}'
             'button,a.btn{background:#7c5ce0;color:#fff;border:0;padding:8px 16px;border-radius:6px;cursor:pointer;text-decoration:none;display:inline-block}'
             '.ok{color:#4ade80}.bad{color:#f87171}.dim{color:#8b8ba0;font-size:13px}</style>')

def _admin_page():
    now = time.time()
    rows = []
    for n, h in HEALTH.items():
        cd = COOLDOWN.get(n, (0, ''))
        cdtxt = f'{max(0,(cd[0]-now))/60:.0f} min · {cd[1]}' if cd[0] > now else '—'
        rows.append(f"<tr><td>{n}</td><td class='ok'>{h.get('ok',0)}</td><td class='bad'>{h.get('fail',0)}</td>"
                    f"<td>{h.get('ultimo','—')}</td><td>{cdtxt}</td></tr>")
    lat = ''.join(f"<tr><td>{k}</td><td>{n2}</td><td>{t/n2:.0f}</td><td>{_pct(list(_rts.get(k,())),.5):.0f}</td>"
                  f"<td>{_pct(list(_rts.get(k,())),.95):.0f}</td><td>{_pct(list(_rts.get(k,())),.99):.0f}</td><td>{x:.0f}</td></tr>"
                  for k, (n2, t, x) in _rtime.items())
    return (f"<html><head><title>admin</title>{_PAGE_CSS}</head><body>"
            f"<h1>Streams Unificados · admin</h1>"
            f"<p class='dim'>sources {sum(1 for m in manifests if m)}/{len(manifests)} up · "
            f"cache streams {len(_cache)} · meta {len(_meta_c)} · cat {len(_cat_c)} · "
            f"<a class='btn' href='/configure'>configure</a></p>"
            f"<h2>Fuentes</h2><table><tr><th>fuente</th><th>ok</th><th>fail</th><th>último</th><th>circuito</th></tr>{''.join(rows)}</table>"
            f"<h2>Latencia por ruta (ms)</h2><table><tr><th>ruta</th><th>n</th><th>avg</th><th>p50</th><th>p95</th><th>p99</th><th>max</th></tr>{lat}</table>"
            f"<h2>Fails por host</h2><p class='dim'>{' · '.join(f'{k}: {v}' for k, v in _fails.items()) or '—'}</p></body></html>")

def _configure_page():
    src_chk = ''.join(
        f"<label style='display:flex;gap:8px;padding:4px 0'><input type='checkbox' name='src' value='{s.name}'"
        f"{' checked' if s.name not in _DISABLED else ''} style='width:auto'> {s.name}</label>"
        for s in SOURCES)
    langs = ','.join(CFG.get('stream_langs') or [])
    opts = ''.join(f"<option value='{v}'{' selected' if CFG.get('debrid_provider')==v else ''}>{v or '— ninguno —'}</option>"
                   for v in ('', 'real-debrid', 'torbox'))
    return (f"<html><head><title>configure</title>{_PAGE_CSS}</head><body>"
            f"<h1>Streams Unificados · configuración</h1>"
            f"<p class='dim'>Guarda en config.json. Tras guardar, POST /reload aplica en caliente.</p>"
            f"<form method='post' action='/configure'>"
            f"<h2>Integraciones</h2>"
            f"<p>Trakt <span class='dim'>(app en trakt.tv/oauth/applications — feed global + personal)</span>"
            f" {'<b class=ok>conectado ✓</b>' if CFG.get('trakt_access_token') else ''}<br>"
            f"<input name='trakt_client_id' value='{CFG.get('trakt_client_id','')}' placeholder='client_id — vacío = Trakt off'>"
            f"<input name='trakt_client_secret' value='' placeholder='client_secret (no se muestra el guardado)'></p>"
            f"{'<p><a class=\"btn\" href=\"/auth/trakt\">Conectar cuenta Trakt</a> <span class=dim>device flow: autorizas una vez en trakt.tv/activate</span></p>' if CFG.get('trakt_client_id') and not CFG.get('trakt_access_token') else ''}"
            f"<p>Debrid <span class='dim'>(reproducción instantánea, sin peers)</span><br>"
            f"<select name='debrid_provider'>{opts}</select><br>"
            f"<input name='debrid_key' value='' placeholder='API key (no se muestra la guardada)'></p>"
            f"<p>M3U propio <span class='dim'>(ruta local, catálogo tv 'Mis canales')</span><br>"
            f"<input name='m3u_path' value='{CFG.get('m3u_path','')}' placeholder='/home/…/canales.m3u'></p>"
            f"<p>Horizonte calendario (días) <input name='horizon_days' value='{CFG.get('horizon_days',45)}' style='width:80px'>"
            f" · idiomas streams <input name='stream_langs' value='{langs}' style='width:200px'></p>"
            f"<h2>Fuentes activas</h2>{src_chk}"
            f"<p><button type='submit'>Guardar</button> <a class='btn' href='/admin'>dashboard</a></p>"
            f"</form></body></html>")

def _trakt_auth_page():
    """Device Authorization: muestra user_code, el usuario lo introduce en
    trakt.tv/activate y el poll en background guarda el token."""
    if not CFG.get('trakt_client_id') or not CFG.get('trakt_client_secret'):
        return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
                f"<h1>Conectar Trakt</h1><p>Faltan credenciales de app: crea una en "
                f"<a href='https://trakt.tv/oauth/applications/new' style='color:#7c5ce0'>"
                f"trakt.tv/oauth/applications/new</a> (redirect URI: <code>urn:ietf:wg:oauth:2.0:oob</code>)"
                f" y guarda client_id + client_secret en <a class='btn' href='/configure'>/configure</a>.</p></body></html>")
    if CFG.get('trakt_access_token'):
        exp = time.strftime('%Y-%m-%d', time.localtime(CFG.get('trakt_expires', 0)))
        return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
                f"<h1>Trakt conectado</h1><p class='ok'>Token activo (expira {exp}).</p>"
                f"<p><a class='btn' href='/configure'>volver</a></p></body></html>")
    d = _trakt_device_start() if not _TRAKT_DEV else _TRAKT_DEV
    if not d:
        return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
                f"<h1>Trakt</h1><p class='bad'>No se pudo iniciar el device flow.</p></body></html>")
    return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
            f"<h1>Autorizar en Trakt</h1>"
            f"<p>1. Abre <a href='{d['verification_url']}' style='color:#7c5ce0'>{d['verification_url']}</a><br>"
            f"2. Introduce el código:</p>"
            f"<p style='font-size:34px;letter-spacing:4px;color:#7c5ce0'><b>{d['user_code']}</b></p>"
            f"<p id='st' class='dim'>esperando autorización…</p>"
            f"<script>setInterval(()=>fetch('/auth/trakt/status').then(r=>r.json()).then(d=>{{"
            f"if(d.state==='ok'){{document.getElementById('st').innerHTML=\"<span class=ok>Conectado — token guardado</span>\";}}"
            f"if(d.state==='none')document.getElementById('st').textContent='expirado — recarga para reintentar';"
            f"}}),4000)</script></body></html>")

_prefetching = False

def _prefetch_catalogs():
    """Calienta _cat_c en background: Discover pide ~170 catálogos a la vez y si
    llegan en frío los upstreams lentos hacen saltar 'Load failed' en el cliente."""
    global _prefetching
    if _prefetching: return
    _prefetching = True
    try:
        fs = [_POOL_BG.submit(handle_catalog, c['type'], c['id'], None)
              for c in build_manifest()['catalogs']]
        for f in fs:
            try: f.result()
            except Exception: pass
    finally:
        _prefetching = False

ROUTES = [
    (re.compile(r'^/stream/([^/]+)/(.+)\.json$'),
     lambda m: handle_stream(m.group(1), m.group(2))),
    (re.compile(r'^/catalog/([^/]+)/([^/]+)/(.+)\.json$'),
     lambda m: handle_catalog(m.group(1), urllib.parse.unquote(m.group(2)), m.group(3))),
    (re.compile(r'^/catalog/([^/]+)/([^/]+)\.json$'),
     lambda m: handle_catalog(m.group(1), urllib.parse.unquote(m.group(2)), None)),
    (re.compile(r'^/meta/([^/]+)/(.+)\.json$'),
     lambda m: handle_meta(m.group(1), m.group(2))),
]

def _refresher():
    while True:
        time.sleep(1800)
        try: refresh_manifests()
        except Exception: pass

if __name__ == '__main__':
    cached = _load_manifests_cache()
    if cached:
        manifests = cached
        threading.Thread(target=refresh_manifests, daemon=True).start()   # fresco en bg
        log_ev('manifests', n=sum(1 for m in manifests if m), total=len(SOURCES), src='cache')
    else:
        refresh_manifests()
        log_ev('manifests', n=sum(1 for m in manifests if m), total=len(SOURCES), src='live')
    threading.Thread(target=_refresher, daemon=True).start()
    threading.Thread(target=_prefetch_catalogs, daemon=True).start()   # caché caliente antes del primer cliente
    class Srv(ThreadingHTTPServer): daemon_threads = True   # sin hilos zombie
    Srv(('127.0.0.1', PORT), H).serve_forever()
