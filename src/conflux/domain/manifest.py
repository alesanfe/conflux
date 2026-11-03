"""Contrato /manifest.json: agrupación de catálogos, tipos, pickers."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import collections
import os
import re

from conflux.core.cfg import _cfgget, _eff_disabled
from conflux.core.state import _YEARS, CFG, SOURCES, manifests
from conflux.domain.lists import _list_defs, _list_fetch

# ---- manifest/catalgos/meta ----
_NO_CATALOG = {'Orion',    # catálogos muertos sin config (metas:[] en Discover)
               'Stravo'}   # catálogos = clones idénticos de StravoII (streams sí aporta)


# Max descriptor size de la colección remota: AllIPTV solo géneros + ES/EN/PT (no 55 idiomas)
_CAT_KEEP = {
    'AllIPTV': re.compile(
        r'^cat_(all|animation\w*|auto|business|classic|comedy|cooking|culture|documentary|'
        r'education|entertainment|family|general|interactive|kids|legislative|lifestyle|'
        r'movies|music|news|outdoor|public|relax|religious|science|series|shop|sports|'
        r'travel|weather|xxx|undefined|spanish|english|portuguese)$', re.IGNORECASE),
}


_PT_ES = (('Filmes', 'Películas'), ('Séries', 'Series'), ('Recentes', 'Recientes'),
          ('Em Alta', 'En Tendencia'), ('Lançamentos', 'Estrenos'), ('Novos', 'Nuevas'))


_ANIME_CAT = re.compile(r'\banim', re.IGNORECASE)      # catálogo declarado movie/series pero es anime

_UTYPE: dict = {}                                # catalog_id -> tipo real en upstream


_GRP_MIN = 2                                   # >=N catálogos de un tipo -> 1 catálogo + selector género

_GRP: dict = {}                                  # cid agrupado -> OrderedDict etiqueta -> (utype, upstream_id, quiere_año)

_YR_OPTS: dict = {}                                  # cid agrupado -> [años] del 2º picker 'year'

_REQ: set = set()                               # cids con extras requeridos upstream: quedan planos

_FEED_EXTRA = frozenset({'lastVideosIds', 'calendarVideosIds'})   # endpoints de app-feed: solo vía sintéticos

_TYPE_ES = {'movie': 'Películas', 'series': 'Series', 'anime': 'Anime',
            'tv': 'TV', 'channel': 'Canales'}


_SYNTH: dict = {}                                       # cid sintético -> (utype, real, extra por defecto)



_EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿️\u200d]+')

def _noemoji(s):
    s2 = _EMOJI.sub('', s)
    s2 = re.sub(r'\s{2,}', ' ', s2).strip(' ·-—|:')
    return s2 or s.strip()


def _eff_rank(n):
    """Orden de catálogos en Discover: config.source_order manda (los listados
    primero, en orden); el resto por _RANK base."""
    order = _cfgget('source_order') or []
    if n in order:
        return min(_RANK.values()) - len(order) + order.index(n)
    return _RANK.get(n, 99)


_RANK = {n: i for i, n in enumerate((          # orden agradable en el selector de Discover
    'Cinemeta', 'NoTorrent', 'TMDB', 'IMDBCat', 'RTomatoes', 'AniSeason', 'TorrentCat',
    'OnePace', 'StravoII', 'Stravo', 'Fenix', 'PublicDom',
    'Kitsu', 'YouTube', 'IPTV', 'AllIPTV'))}


def _cat_name(src, c):
    """'Kitsu·Kitsu' -> 'Kitsu·List' · 'MAL: X' -> 'MAL·X' · 'AllIPTV·All IPTV X' -> 'AllIPTV·X' · PT->ES."""
    n = (c.get('name') or c['id']).strip()
    flex = r'\s*'.join(map(re.escape, src))                                 # 'AllIPTV' casa 'All IPTV'
    n = re.sub(rf'^{flex}[:·\s\-.]+', '', n, flags=re.IGNORECASE).strip()             # prefijo redundante
    n = re.sub(r'^tmdb\.', '', n, flags=re.IGNORECASE).strip()                         # tmdb.top -> top
    if not n or n.lower() == src.lower():
        n = c['id'].rsplit('-', 1)[-1].replace('.', ' ').title()
        if n.lower() == src.lower():
            return src
    for pt, es in _PT_ES:
        n = re.sub(rf'\b{pt}\b', es, n)
    return f'{src} · {_noemoji(n)}'


def _mf_cat_req(c):
    """extras requeridos servibles (genre/ids-de-app) -> lista; None si el
    catálogo exige algo que el cliente no puede rellenar ('search','skip'...)."""
    req = list(c.get('extraRequired') or []) + \
          [e.get('name') for e in (c.get('extra') or []) if e.get('isRequired')]
    # 'search'/'skip' requeridos -> opción muerta; lastVideosIds/calendarVideosIds
    # los aporta LA APP desde tu biblioteca (Continue Watching / Calendario)
    if any(r not in ('genre', 'lastVideosIds', 'calendarVideosIds') for r in req):
        return None
    return req


def _mf_one_cat(src, c, keep, seen):
    """Un catálogo upstream -> (rank, nc) listo para el manifest, o None si
    no es servible. Marca _REQ/_UTYPE y reclasifica anime."""
    if keep and not keep.search(c['id']):
        return None
    req = _mf_cat_req(c)
    if req is None or _FEED_EXTRA & set(req) or c['id'].endswith('_filter'):
        return None                     # feed-app/filtro: no es lista navegable
    cid = f"s{src.name.lower()}_{c['type']}_{c['id']}"  # tipo upstream en slug: único
    if cid in seen:
        return None                              # upstream repite el mismo catálogo
    seen.add(cid)
    _UTYPE[cid] = c['type']                            # tipo real upstream
    if req:
        _REQ.add(cid)                            # su selector nativo importa: no agrupar
    return (_eff_rank(src.name), _mf_nc(src, c, cid))


def _mf_nc(src, c, cid):
    """Catálogo upstream -> nc del manifest (id renombrado, nombre, reclasif anime)."""
    nc = dict(c, id=cid, name=_cat_name(src.name, c))
    if nc.get('extraRequired'):
        del nc['extraRequired']  # 'genre' opcional: grid sin obligar filtro
    if c['type'] in ('movie', 'series') and _ANIME_CAT.search(f"{c.get('name', '')} {c['id']}"):
        nc['type'] = 'anime'                           # contenido anime -> Discover anime
    return nc


def _mf_collect_cats():
    """Catálogos upstream elegibles -> [(rank, nc)]. Marca _REQ/_UTYPE/_ANIME."""
    cats: list = []
    seen: set = set()
    for i, m in enumerate(manifests):
        if i >= len(SOURCES):   # caché de manifests más larga que SOURCES (fuentes quitadas)
            break
        if not m or SOURCES[i].name in _NO_CATALOG or SOURCES[i].name in _eff_disabled():
            continue
        keep = _CAT_KEEP.get(SOURCES[i].name)
        cats += [r for c in m.get('catalogs', [])
                 if (r := _mf_one_cat(SOURCES[i], c, keep, seen))]
    return cats


def _mf_year_opts(nc):
    """Si el único extra requerido es genre=año -> sus opciones; si no, None."""
    reqs = {e.get('name') for e in (nc.get('extra') or []) if e.get('isRequired')}
    yopts = next((e.get('options') for e in nc['extra'] if e.get('name') == 'genre'), None)
    if reqs == {'genre'} and _year_opts(yopts):
        return yopts
    return None


def _year_opts(yopts):
    """Todas las opciones de genre son años de 4 dígitos."""
    return bool(yopts) and all(re.fullmatch(r'\d{4}', o) for o in yopts)


def _mf_year_fold(members):
    """Miembros _REQ cuyo único extra requerido es genre=año se pliegan al
    grupo con un 2º picker 'year' con las opciones reales upstream."""
    members_in, year_opts = [], set()
    for r, nc in members:
        wants = nc['id'] in _REQ and _mf_year_opts(nc)
        if wants:
            year_opts.update(wants)
        members_in.append((r, nc, bool(wants)))
    return members_in, year_opts


def _mf_grp_extra(opts, year_opts, members_in):
    up_extras = {e.get('name') for r, nc, _ in members_in for e in (nc.get('extra') or [])} - {'genre'}
    extra = [{'name': 'genre', 'isRequired': False, 'options': list(opts)}]
    if year_opts:
        extra.append({'name': 'year', 'isRequired': False, 'options': year_opts})
    extra += [{'name': n, 'isRequired': False} for n in ('search', 'skip') if n in up_extras]
    return extra


def _mf_grp_opts(members_in):
    """label único -> (ut, real_id, wants_year, genre_forzado) por miembro del
    grupo. Los miembros con géneros propios (no-año) exponen 'Label · sub' para
    que sus vistas por género sigan alcanzables plegadas — sin ello el grupo
    pedía el catálogo pelado y las vistas por género quedaban muertas."""
    opts = collections.OrderedDict()
    for _r, nc, wants in members_in:
        label = _noemoji(nc['name'].split(' · ', 1)[-1] or nc['id'])
        ut, _, real = nc['id'].split('_', 1)[1].partition('_')
        if label in opts:
            label = f"{label} ({ut.title()})"
        while label in opts:
            label += ' ·'
        opts[label] = (ut, real, wants, None)
        subs = next((e.get('options') for e in (nc.get('extra') or [])
                     if e.get('name') == 'genre'), None)
        if subs and not _year_opts(subs):                # género=año ya lo cubre el picker year
            for s in subs[:20]:                          # cota: el picker no explota
                sub = f'{label} · {s}'
                while sub in opts:
                    sub += ' ·'
                opts[sub] = (ut, real, wants, s)
    return opts


def _mf_group(cats):
    """Fuentes con muchos catálogos de un tipo -> un catálogo con 2º selector."""
    bytype = collections.defaultdict(list)
    for rank, nc in cats:
        src = nc['id'].split('_', 1)[0][1:]                  # 'sstravoii' -> 'stravoii'
        bytype[(src, nc['type'])].append((rank, nc))
    out = []
    for (src, dtype), members in bytype.items():
        members_in, year_opts = _mf_year_fold(members)
        if len(members_in) < _GRP_MIN:
            out.extend(members)
            continue
        opts = _mf_grp_opts(members_in)
        gid = f"s{src}_{dtype}_grp"
        _GRP[gid] = opts
        _YR_OPTS[gid] = sorted(year_opts, reverse=True)
        out.append((members[0][0], {'id': gid, 'type': dtype,
                    'name': members[0][1]['name'].split(' · ')[0],
                    'extra': _mf_grp_extra(opts, _YR_OPTS[gid], members_in)}))
        folded = {nc['id'] for r, nc, _ in members_in}
        out.extend((r, nc) for r, nc in members if nc['id'] not in folded)  # resto _REQ: planos
    return out


def _mf_synth_cats(cats):
    """Catálogos sintéticos: estrenos, calendario-estrenos, búsqueda global,
    listas externas, 'porque viste' Trakt, canales M3U."""
    _mf_cal_cats(cats)
    _mf_search_cats(cats)
    _mf_list_cats(cats)
    _mf_recom_cats(cats)
    _mf_m3u_cat(cats)


def _mf_cal_cats(cats):
    """'Estrenos' (cinemeta year+genre) + 'Próximos estrenos' en calendario.
    Este último va declarado 'series' porque el calendario solo consulta
    catálogos series — los metas siguen type=movie y la ficha abre bien."""
    ecid = 'scinemeta_movie_estrenos'
    _SYNTH[ecid] = ('movie', 'year', f'genre={_YEARS[0]}')
    cats.append((_eff_rank('Cinemeta'), {'id': ecid, 'type': 'movie',
                 'name': 'Cinemeta · Estrenos',
                 'extra': [{'name': 'genre', 'isRequired': False, 'options': _YEARS}]}))
    ccid = 'scinemeta_series_estrenoscal'
    _SYNTH[ccid] = ('movie', '_estrenos_cal', None)
    cats.append((_eff_rank('Cinemeta'), {'id': ccid, 'type': 'series',
                 'name': 'Cinemeta · Próximos estrenos',
                 'extra': [{'name': 'calendarVideosIds', 'isRequired': True}]}))
    bcid = 'scinemeta_series_calendario'               # gemelo navegable: el mismo
    _SYNTH[bcid] = ('movie', '_estrenos_cal', None)    # feed en Board/Discover (el
    cats.append((_eff_rank('Cinemeta'), {'id': bcid, 'type': 'series',
                 'name': 'Cinemeta · Calendario estrenos'}))  # cal solo lo pide la vista Calendario)
    lvid = 'scinemeta_series_lastvids'             # Continue Watching: ids biblio -> tt -> cinemeta
    _SYNTH[lvid] = ('series', 'last-videos', None)
    cats.append((_eff_rank('Cinemeta'), {'id': lvid, 'type': 'series',
                 'name': 'Cinemeta · Últimos vídeos',
                 'extra': [{'name': 'lastVideosIds', 'isRequired': True}]}))


def _mf_search_cats(cats):
    """Búsqueda global: un catálogo por tipo que hace fan-out a todas las fuentes."""
    for t in ('movie', 'series', 'anime'):
        gid = f'suniform_{t}_search'
        _SYNTH[gid] = (t, '_busqueda', None)
        cats.append((_eff_rank('Cinemeta'), {'id': gid, 'type': t,
                     'name': 'Buscar todo',
                     'extra': [{'name': 'search', 'isRequired': True}]}))


def _mf_list_cats(cats):
    """Listas externas como catálogos (config.catalog_lists)."""
    for i, url in enumerate(_list_defs()):
        lname, items = _list_fetch(url)
        for t in sorted({t for t, _ in items}):
            rid = f'slist{i}_{t}'
            _SYNTH[rid] = (t, '_list', url)
            cats.append((_eff_rank('Cinemeta') + 1, {'id': rid, 'type': t,
                         'name': f'☰ {lname}'}))


def _mf_recom_cats(cats):
    """'Porque viste X' — solo con cuenta Trakt conectada (/auth/trakt)."""
    if not CFG.get('trakt_access_token'):
        return
    for t in ('movie', 'series'):
        rid = f'suniform_{t}_recom'
        _SYNTH[rid] = (t, '_recom', None)
        cats.append((_eff_rank('Cinemeta'), {'id': rid, 'type': t,
                     'name': 'Recomendados · tu Trakt'}))


def _mf_m3u_cat(cats):
    """Canal M3U propio (config.m3u_path)."""
    if CFG.get('m3u_path') and os.path.exists(CFG['m3u_path']):
        mid = 'sm3u_tv_channels'
        _SYNTH[mid] = ('tv', '_m3u', None)
        cats.append((_eff_rank('IPTV'), {'id': mid, 'type': 'tv',
                     'name': 'Mis canales',
                     'extra': [{'name': 'date', 'isRequired': False}]}))


def _mf_dedup_names(cats):
    """Nombres duplicados dentro del mismo tipo -> sufijo '(UpstreamType n)'."""
    counts = collections.Counter((c['name'], c['type']) for c in cats)
    taken: dict = {}
    for nc in cats:
        n = nc['name']
        if counts[(n, nc['type'])] > 1:
            u = _UTYPE.get(nc['id'], nc['type']).title()
            k = (n, u)
            taken[k] = taken.get(k, 0) + 1
            nc['name'] = f"{n} ({u}{f' {taken[k]}' if taken[k] > 1 else ''})"


def build_manifest():
    """Manifest unificado: catálogos upstream reagrupados + sintéticos."""
    cats = _mf_group(_mf_collect_cats())
    _mf_synth_cats(cats)
    _catoff = set(_cfgget('catalogs_disabled') or [])
    cats = [nc for _, nc in sorted(cats, key=lambda t: t[0]) if nc['id'] not in _catoff]
    _mf_dedup_names(cats)
    return {
        'id': 'local.uniform.streams', 'version': '1.0.0',
        'name': 'Streams Unificados',
        'description': 'Streams de todos los addons con formato uniforme + catálogos fusionados',
        'resources': (['catalog', 'meta', 'stream']
                      + (['subtitles'] if _cfgget('opensubtitles_key') else [])),
        'types': ['movie', 'series', 'anime', 'channel', 'tv'],
        'catalogs': cats,
        'idPrefixes': ['tt', 'kitsu', 'mal', 'anilist', 'anidb', 'tmdb', 'tvdb', 'ia', 'tpb', 'pluto', 'pd', 'yt', 'yt_id', 'iptv', 'local', 'mx', '_onepace', 'onepace', 'm3u', 'dlstreams', 'essential'],
        'behaviorHints': {'newEpisodeNotifications': True,
                          'configurable': True,               # engranaje -> abre <base>/configure
                          # Native EPG: solo si el catálogo guía existe (spec SDK)
                          **({'epgProvider': True}
                             if any(c['id'] == 'sm3u_tv_channels' for c in cats) else {})},
    }


_LEGACY = ['Torrentio','TorrentsDB','MediaFusion','Peerflix','Meteor','Ytztvio',
           'NoTorrent','Orion','PublicDom']   # orden de ids antiguos sN_ (cacheados por la app)
