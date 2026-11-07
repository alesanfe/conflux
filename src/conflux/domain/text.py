"""Parseo/normalización de streams (calidad, seeds, spam, nombres)."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import re

from conflux.core.cfg import _cfgget, _es_pat
from conflux.domain.sources import res_of, res_prefixes, res_types

# ---- normalizacion de streams ----
RE_RES   = re.compile(r'\b(4320p|2160p|1440p|1080p|720p|576p|480p|360p|240p|8K|4K|HD|SD)\b', re.IGNORECASE)

RE_SIZE  = re.compile(r'(\d+(?:[.,]\d+)?)\s?(GB|GiB|MB|MiB|TB)\b', re.IGNORECASE)

RE_SEEDS = re.compile(r'(?:👤|seed(?:er)?s?|peers?)\s*[:=]?\s*(\d{1,6})|(\d{1,6})\s*(?:seed(?:er)?s?|peers?)\b', re.IGNORECASE)

RE_SRC   = re.compile(r'[⚙️🔎☁️]\s*([\w.\-]+)')

RE_LANG  = re.compile(r'\b(Multi\s?Audio|Multi|Dual\s?Audio|Dual|LATINO|Latino|CASTELLANO|Castellano|ESPAÑOL|Español|Spanish|SPANISH|ESP|ES|ENGLISH|English|ENG|EN|VOSE|FRENCH|French|GERMAN|ITALIAN|PORTUGUESE|JAPANESE|KOREAN|HINDI|RUS|Rutor)\b', re.IGNORECASE)

RE_CODEC = re.compile(r'\b(HEVC|x\.?265|h\.?265|x\.?264|h\.?264|AVC|AV1|VP9|XviD)\b', re.IGNORECASE)

RE_AUDIO = re.compile(r'\b(Dolby\s?Atmos|Atmos|DDP?5\.1|DDP|DD\+|TrueHD|DTS(?:-HD|-X)?|E-?AC-?3|EC3|FLAC|AAC2?\.0|AAC|[257]\.1|Stereo|Mono)\b', re.IGNORECASE)

RE_DUR   = re.compile(r'(\d+)\s?h\s?(\d+)\s?m|(\d{1,2}):(\d{2}):\d{2}|(\d+)\s?min', re.IGNORECASE)

FLAGS    = re.compile(r'[\U0001F1E6-\U0001F1FF]{2}')

RE_SRCT  = re.compile(r'\b(BDREMUX|REMUX|BluRay|BDRip|BRRip|WEB-?DL|WEBRip|HDTV|DVDRip|DVD|CAM|HDCAM|TS|SCR)\b', re.IGNORECASE)

RE_HDR   = re.compile(r'\b(HDR10\+|Dolby\s?Vision|HDR10|HLG|HDR|DV)\b', re.IGNORECASE)


LANG_MAP = {'EN':'Inglés','ENG':'Inglés','ENGLISH':'Inglés','ES':'Español','ESP':'Español',
            'SPANISH':'Español','ESPAÑOL':'Español','CASTELLANO':'Castellano','LATINO':'Latino',
            'MULTI':'Multi','MULTI AUDIO':'Multi','DUAL':'Dual','DUAL AUDIO':'Dual',
            'JAPANESE':'Japonés','FRENCH':'Francés','GERMAN':'Alemán','ITALIAN':'Italiano',
            'PORTUGUESE':'Portugués','KOREAN':'Coreano','HINDI':'Hindi','RUS':'Ruso','RUTOR':'Ruso'}

AUDIO_MAP = {'ATMOS':'Atmos','DOLBY ATMOS':'Atmos','TRUEHD':'TrueHD','DDP5.1':'DD+ 5.1','DD5.1':'DD 5.1',
             'DD+':'DD+','DDP':'DD+','EAC3':'EAC3','E-AC-3':'EAC3','EC3':'EAC3','DTS-HD':'DTS-HD',
             'DTS-X':'DTS:X','DTS':'DTS','FLAC':'FLAC','AAC2.0':'AAC 2.0',
             'AAC':'AAC','STEREO':'Stereo','MONO':'Mono','2.1':'2.1','5.1':'5.1','7.1':'7.1'}

INDEXER_MAP = {'NYAA':'NyaaSi','NYAASI':'NyaaSi','RUTRACKER':'Rutracker','WOLFMAX4K':'Wolfmax4k',
 'FRAMESTOR':'FraMeSToR','YTS':'YTS','1337X':'1337x','EZTV':'EZTV','RARBG':'RARBG',
 'THEPIRATEBAY':'TPB','TPB':'TPB','TGX':'TGx','TORRENTGALAXY':'TGx',
 'KICKASS':'KickAss','HORRIBLESUBS':'HorribleSubs','ERAI-RAWS':'Erai-raws','BITSEARCH':'BitSearch',
 'PIXELDRAIN':'Pixeldrain','FASTPLAY':'FastPlay','ASTRA':'Astra','SPIDER':'Spider','FILEMOOD':'filemood',
 'RUTOR':'RuTor','MEGUSTA':'MeGusta','NTB':'NTb','FGT':'FGT','EVO':'EVO','PSA':'PSA','ION10':'ION10',
 'XNT':'XNT','DDR':'DDR','SUCCESSFULCRAB':'SuccessfulCrab','SMURF':'SMURF','VXT':'VxT'}

SRCT_ORD = ['BDREMUX','REMUX','BLURAY','WEB-DL','WEBRIP','BDRIP','BRRIP','HDTV','DVDRIP','DVD','CAM','HDCAM','TS','SCR']

# La columna de stream-card del cliente corta ~25 chars: etiquetas cortas
# para que 'REMUX'/'BD' no salgan mutilados ('REMU','BDREMU' parecen typos).
_SRCT_DISP = {'BDREMUX': 'REMUX', 'BLURAY': 'BD', 'DVDRIP': 'DVD', 'HDCAM': 'CAM'}

HDR_ORD  = ['HDR10+','DV','HDR10','HLG','HDR']

# Puntos de calidad para _qscore — resolución y cached siguen dominando el
# ranking; esto decide dentro de la misma res: REMUX > WEBRip, DV+Atmos > AAC.
_RIP_SC   = {'BDREMUX': 80, 'REMUX': 80, 'BLURAY': 70, 'BDRIP': 65, 'BRRIP': 60,
             'WEB-DL': 55, 'WEBRIP': 40, 'HDTV': 25, 'DVDRIP': 15, 'DVD': 10}
_HDR_SC   = {'DV': 30, 'HDR10+': 28, 'HDR10': 22, 'HLG': 18, 'HDR': 18}
_CODEC_SC = {'AV1': 15, 'HEVC': 12, 'X265': 12, 'H265': 12, 'H.265': 12,
             'AVC': 6, 'X264': 6, 'H264': 6, 'H.264': 6, 'VP9': 6, 'XVID': 0}
_AUDIO_SC = {'Atmos': 25, 'DTS:X': 20, 'TrueHD': 18, 'DTS-HD': 16, 'DTS': 12,
             'EAC3': 10, 'DD+ 5.1': 10, 'DD 5.1': 8, 'DD+': 8, 'FLAC': 10,
             'AAC 2.0': 4, 'AAC': 4, '7.1': 4, '5.1': 2, '2.1': 1}


def _qscore(f):
    """Puntos de calidad del release parseado (rip + hdr + codec + mejor audio,
    0..~150). Los campos ya se extraían para el display — ahora también ordenan."""
    au = (f.get('audio') or '').split()
    return (_RIP_SC.get(f.get('srctype') or '', 0)
            + _HDR_SC.get(f.get('hdr') or '', 0)
            + _CODEC_SC.get((f.get('codec') or '').upper(), 0)
            + max((_AUDIO_SC.get(t, 0) for t in au), default=0))


def _prio(found, order):
    s = {f.upper().replace('DOLBY VISION','DV').replace('WEBDL','WEB-DL') for f in found}
    return next((o for o in order if o in s), '')


def parse_size(t):
    """'1.4 GB'/'700 MB' en texto libre -> GB (float)."""
    m = RE_SIZE.search(t)
    if not m:
        return None
    v = float(m.group(1).replace(',', '.'))
    u = m.group(2).upper()
    if u.startswith('M'):
        v /= 1024
    if u.startswith('T'):
        v *= 1024
    return v


def parse_seeds(t):
    """'👤 123'/'seeders: 45' en texto libre -> int."""
    m = RE_SEEDS.search(t)
    if not m:
        return None
    return int(next(g for g in m.groups() if g))


def _ef_lang(raw, text):
    """Idioma: emojis-flags del título o tokens ES/EN/… via LANG_MAP."""
    fl = list(dict.fromkeys(FLAGS.findall(raw)))
    if fl:
        return ' '.join(fl[:6])
    ls = [x for x in dict.fromkeys(LANG_MAP.get(g.upper(), '') for g in RE_LANG.findall(text)) if x]
    return 'Multi' if 'Multi' in ls or len(ls) > 2 else ' / '.join(ls)


def _ef_audio(text):
    au = list(dict.fromkeys(AUDIO_MAP.get(x.upper(), x.upper().title()) for x in RE_AUDIO.findall(text)))
    au.sort(key=lambda a: 1 if re.fullmatch(r'[257]\.1', a) else 0)    # marca antes que canales
    # canales adyacentes con '/' — '7.1 5.1' se lee '7.15.1' en la card
    return re.sub(r'(?<=\d\.\d) (?=\d\.\d)', '/', ' '.join(au[:3]))


def _ef_dur(bh, text):
    durm = RE_DUR.search(text)
    dur = _ef_dur_txt(durm)
    if not dur and isinstance(bh.get('duration'), (int, float)) and bh['duration'] > 60:
        ds = int(bh['duration'])
        dur = f"{ds//3600}h{ds%3600//60}m"   # Meteor: duración real
    return dur


def _ef_dur_txt(durm):
    """RE_DUR match -> 'HhMMm' / 'H:MM' / 'MMm' segun la alternativa."""
    if not durm:
        return ''
    if durm.group(1):
        return f"{durm.group(1)}h{durm.group(2)}m"
    if durm.group(3):
        return f"{durm.group(3)}:{durm.group(4)}"
    return f"{durm.group(5)}m"


def _ef_res(text):
    rm = RE_RES.search(text)
    return rm.group(1).upper().replace('4K', '2160P').replace('8K', '4320P') if rm else 'SD'


def _ef_size(bh, text):
    size = parse_size(text)
    if size is None and bh.get('videoSize'):
        size = bh['videoSize'] / 1e9
    return size


def _ef_seeds(bh, text):
    seeds = parse_seeds(text)
    if isinstance(bh.get('seeders'), int) and not isinstance(bh.get('seeders'), bool):
        seeds = bh['seeders']                                          # Meteor: dato real
    return seeds


def _ef_indexer(text):
    srcm = RE_SRC.search(text)
    return next((g.strip() for g in srcm.groups() if g), None) if srcm else None


def _ef_text(s):
    """Texto completo del stream para regex: title/name + hints de archivo."""
    bh = s.get('behaviorHints') or {}
    if not isinstance(bh, dict):                     # upstream puede mandar cualquier cosa
        bh = {}
    raw = next((v for k in ('title', 'description', 'name')
                if isinstance((v := s.get(k)), str) and v), '').strip()
    parts = [raw, s.get('name'), bh.get('filename'), bh.get('bingeGroup')]
    text = ' '.join(x for x in parts if isinstance(x, str) and x)
    return bh, raw, text


def extract_fields(s):
    """Stream crudo → dict de campos parseados (res, seeds, size, indexer…)."""
    bh, raw, text = _ef_text(s)
    res  = _ef_res(text)
    codecm = RE_CODEC.search(text)
    return {
        'res': res, 'disp': res[:-1]+'p' if res.endswith('P') else res,
        'size': _ef_size(bh, text), 'seeds': _ef_seeds(bh, text),
        'indexer': _ef_indexer(text), 'text': text,
        'lang': _ef_lang(raw, text), 'audio': _ef_audio(text),
        'codec': codecm.group(1).upper() if codecm else '', 'dur': _ef_dur(bh, text),
        'cached': bool(bh.get('cached') or '☁️' in text or 'debrid' in text.lower()),
        'srctype': _prio(RE_SRCT.findall(text), SRCT_ORD),
        'hdr': _prio(RE_HDR.findall(text), HDR_ORD),
        'es': bool(_es_pat().search(text)),
    }


_CODEC_TOKENS = {'X265','X264','H265','H264','HEVC','AV1','VP9','AVC','10BIT','AAC','DTS'}


def _indexer_of(f, short):
    idx = f['indexer'] or short
    if idx.upper() in _CODEC_TOKENS:
        idx = short
    if idx == short:                                               # NoTorrent: hoster = indexer
        hm = re.search(r'([A-Za-z0-9]+)\s*-\s*Notorrent', f['text'])
        if hm:
            idx = hm.group(1)
    return INDEXER_MAP.get(idx.upper().replace(' ',''), idx)


def _display_t1(f):
    t1 = []
    if f['cached']:
        t1.append('⚡ En caché')
    elif f['seeds'] is None:
        t1.append('🔗 directo')
    else:
        t1.append(f'👤 {f["seeds"]}')
    if f['size']:
        t1.append(f'💾 {f["size"]:.1f} GB')
    if f['codec']:
        t1.append(f'🎞 {f["codec"]}')
    return t1


def _display_t2(f, short, idx):
    # rip-type delante del indexer: si la columna estrecha recorta, corta
    # 'Torrentio/1337…' (aún legible) y no 'REMU'/'BDREMU' (parecen typos).
    t2 = [f'⚙️ {short}']
    if f['srctype']:
        t2.append(_SRCT_DISP.get(f['srctype'], f['srctype']))
    if idx.lower() != short.lower():
        t2.append(idx)
    if f['es']:
        t2.append('ES')
    if f['dur']:
        t2.append(f'⏱ {f["dur"]}')
    return t2


class _FmtSafe(dict):
    """format_map: clave desconocida -> '' (nunca KeyError con templates raros)."""
    def __missing__(self, k):
        return ''


def _strip_seps(out):
    """Separadores huérfanos fuera (UX-001): ' · ' repetido -> uno; un ' · '
    pegado a un borde de línea (token vacío adyacente) se come; líneas que
    quedan vacías se colapsan. Sin esto un template con campos ausentes
    renderizaba '· ·' y líneas que empiezan por '· '."""
    out = re.sub(r'( · )?( · )+', ' · ', out)             # ' ·  · ' -> ' · '
    out = re.sub(r'(?:^|\n) · | · (?=\n|$)', '', out)     # separador al borde
    return re.sub(r'\n{2,}', '\n', out).strip(' ·\n')     # líneas vacías fuera


def _clip_lines(s, budget):
    """Presupuesto por línea (UX-006): corta en el último separador antes del
    límite; sin separador, corte duro + '…'. stremio-web clipea de todos
    modos — cortar en el separador deja una línea que termina limpia."""
    if not budget or budget <= 0:
        return s
    out = []
    for ln in s.split('\n'):
        if len(ln) <= budget:
            out.append(ln)
            continue
        cut = ln.rfind(' · ', 0, budget)
        out.append((ln[:cut] if cut > 20 else ln[:budget - 1] + '…'))
    return '\n'.join(out)


def _display_name(f):
    tmpl = _cfgget('stream_format', '')                  # formatter configurable (estilo AIOStreams)
    if tmpl:
        try:
            out = tmpl.format_map(_FmtSafe(
                res=f.get('disp', ''), hdr=f.get('hdr', ''), codec=f.get('codec', ''),
                audio=f.get('audio', ''), lang=f.get('lang', ''),
                rip=f.get('srctype', ''), title=f.get('text', ''),
                es='ES' if f.get('es') else ''))
            out = _strip_seps(out)
            if out:
                return out
        except Exception:                                # noqa: BLE001 — template roto -> formato normal
            pass
    l1 = ' · '.join(x for x in [f['lang'], f['audio']] if x)
    rows = ['☁️ ' + ' · '.join(x for x in [f['disp'], f['hdr']] if x)]
    if l1:
        rows.append('🔊 ' + l1)
    return '\n'.join(rows)


def build_display(f, short):
    """Campos parseados → (name, title) en el formato compacto de 2+2 líneas;
    stream_max_chars (default 200, 0=off) es presupuesto POR LÍNEA (UX-006)."""
    idx = _indexer_of(f, short)
    b = _cfgget('stream_max_chars', 200) or 0
    return _clip_lines(_display_name(f), b), \
           _clip_lines('\n'.join(' · '.join(x)
                                 for x in [_display_t1(f), _display_t2(f, short, idx)] if x), b)


def _san_fileidx(out):
    """fileIdx debe ser int (bool es int en py: true también se corrige)."""
    if 'fileIdx' in out and (isinstance(out['fileIdx'], bool) or not isinstance(out['fileIdx'], int)):
        try:
            out['fileIdx'] = int(out['fileIdx'])
        except (TypeError, ValueError):
            out.pop('fileIdx')


_SAN_TYPES = (('infoHash', str), ('url', str), ('externalUrl', str), ('ytId', str),
              ('sources', list), ('subtitles', list), ('behaviorHints', dict))


def sanitize_stream(s):
    """Copia del stream con tipos válidos para stremio-web (un fileIdx:null rompe todo)."""
    out = {k: v for k, v in dict(s).items() if v is not None}
    _san_fileidx(out)
    for k, t in _SAN_TYPES:
        if k in out and not isinstance(out[k], t):
            out.pop(k)
    if 'infoHash' in out:
        out['infoHash'] = out['infoHash'].lower()
        out.setdefault('fileIdx', 0)          # como Torrentio: siempre idx
    return out


def norm_stream(s, short):
    """Pipeline: parsea → sanitiza → formatea. Devuelve
    (stream, res, seeds_crudos, size, cached, campos, short) — campos+short
    permiten re-etiquetar por perfil sin re-parsear el caché compartido."""
    f = extract_fields(s)
    out = sanitize_stream(s)
    out['name'], out['title'] = build_display(f, short)
    return out, f['res'], f['seeds'] or 0, f['size'] or 0, f['cached'], f, short


RANK = {'4320P': 9, '2160P': 8, '1440P': 7, '1080P': 6, 'HD': 6, '720P': 5, '576P': 4, '480P': 3, '360P': 2, '240P': 1, 'SD': 1}

_RE_SPAM   = re.compile(r'daily limit|limit reached|link limit|upgrade (your|to|premium|account)|premium streams|free trial|join our discord|discord\.gg', re.IGNORECASE)


def _id_prefix(sid):
    """Prefijo de un id de stremio = letras/underscore iniciales.
    'tt0903747:5:1'->'tt' · 'kitsu:49847:2'->'kitsu' · 'mx:1:slug'->'mx' · '_onepace1'->'_onepace'
    (nunca split(':'): los imdb llevan el prefijo pegado a dígitos)"""
    m = re.match(r'[a-zA-Z_]+', sid or '')
    return m.group() if m else (sid or '').split(':')[0]


def _spam(s):
    raw0 = next((v for k in ('title', 'description')
                 if isinstance((v := s.get(k)), str) and v), '')
    rawn = s.get('name') if isinstance(s.get('name'), str) else ''
    return bool(raw0.lstrip().startswith(('⚠','🔒')) or rawn.lstrip().startswith(('⚠','🔒'))
        or 'ERROR!' in raw0 or _RE_SPAM.search(raw0))


def _ad_link(s):
    """externalUrl como ÚNICO enlace = promo/pago/config, jamás contenido
    reproducible (corpus real: payments.stravo.site, t.me, discord.gg,
    paypal). Con url/infoHash/ytId presentes el stream es real y pasa."""
    return 'externalUrl' in s and not (
        s.get('infoHash') or s.get('url') or s.get('ytId'))


_RE_BTIH = re.compile(r'[0-9a-f]{40}', re.IGNORECASE)


def _playable(s):
    """¿Tiene localizador reproducible? (STM-001) Sin él, la card en Stremio es
    una fila muerta que el cliente no puede abrir. Válidos: infoHash BTIH
    40-hex, url http(s)/magnet, o ytId. Un infoHash malformado NO invalida una
    url directa válida (debrid). externalUrl solo NO es reproducible (→ _ad_link)."""
    if s.get('ytId'):
        return True
    u = s.get('url')
    if isinstance(u, str) and (u.startswith('http') or u.startswith('magnet:')):
        return True
    h = s.get('infoHash')
    return isinstance(h, str) and bool(_RE_BTIH.fullmatch(h))


def _skip_manifest(m, stype, sid):
    """Lo que el manifest declara que NO cubre la fuente para este (tipo,id)."""
    if m is not None and m.get('resources') and 'stream' not in res_of(m):
        return 'manifest sin stream'
    ts = res_types(m, 'stream')
    if ts is not None and stype not in ts:                        # PublicDom: solo movies
        return 'tipo no soportado'
    prefs = res_prefixes(m, 'stream')
    if prefs is not None and _id_prefix(sid) not in prefs:        # Torrentio solo habla tt/kitsu
        return 'prefijo no soportado'
    return None
