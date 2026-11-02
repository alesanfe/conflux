"""AniList GraphQL: airing schedule de la temporada."""
# pylint: disable=broad-exception-caught,line-too-long
import time
from string import Template

import requests

from .net import _session

# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido



def _anilist_row(a):
    """airingSchedule -> {ep, airingAt, idMal, img, title} o None sin idMal."""
    m = a.get('media') or {}
    if not m.get('idMal'):
        return None
    return {'ep': a.get('episode'), 'airingAt': a.get('airingAt'),
            'idMal': m['idMal'], 'img': (m.get('coverImage') or {}).get('large'),
            'title': (m.get('title') or {}).get('english')
                     or (m.get('title') or {}).get('romaji')}



def _anilist_airing(days):
    """Próximas emisiones de anime (AniList GraphQL, sin auth):
    [{ep, airingAt, idMal, title, img}] ordenadas por fecha."""
    out, seen = [], set()
    t_end = int(time.time()) + days * 86400
    for page in (1, 2, 3):
        rows = _anilist_page(page, t_end)
        if not rows:
            break
        for a in rows:
            row = _anilist_row(a)
            if not row or (row['idMal'], row['ep']) in seen:
                continue
            seen.add((row['idMal'], row['ep']))
            out.append(row)
    return out

def _anilist_page(page, t_end):
    """Una página de airingSchedules (o [] en error/fin)."""
    q = {'query': Template('{Page(page:$page,perPage:50){airingSchedules(notYetAired:true,'
                           'airingAt_lesser:$t_end,sort:TIME){airingAt episode media{idMal '
                           'title{romaji english} coverImage{large}}}}}')
         .substitute(page=page, t_end=t_end)}           # Template: las llaves GraphQL chocan con f-string
    try:
        r = _session('graphql.anilist.co').post('https://graphql.anilist.co',
            json=q, timeout=8).json()
    except (requests.RequestException, ValueError):
        return []
    return ((r.get('data') or {}).get('Page') or {}).get('airingSchedules') or []
