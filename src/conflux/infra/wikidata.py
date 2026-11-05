"""Wikidata: claims de canales YouTube (país, web, social, año)."""
# pylint: disable=broad-exception-caught,line-too-long

from conflux.core.state import CLOCK, as_dict
from conflux.infra.net import fetch

# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido



def _pv(claims, prop):
    for s in claims.get(prop, []):
        v = s.get('mainsnak', {}).get('datavalue', {}).get('value')
        if v is not None:
            return v
    return None



_wd_c: dict = {}                                    # yt -> (ts, dict) — TTL 24h: no cambia a diario



def _wikidata(yt):
    """Canal yt -> datos reales de Wikidata (country/website/socials/year) por P2397."""
    if len(_wd_c) > 400:                                  # cota: purga mitad más vieja
        for k in sorted(_wd_c, key=lambda k: _wd_c[k][0])[:200]:
            _wd_c.pop(k, None)
    ent = _wd_c.get(yt)
    if ent and CLOCK.time() - ent[0] < 86400:
        return ent[1]
    out = {}
    d = fetch(f'https://www.wikidata.org/w/api.php?action=query&list=search'
              f'&srsearch=haswbstatement:P2397={yt}&format=json&srlimit=1', 6)
    q = (as_dict(d).get('query', {}).get('search') or [{}])[0].get('title')
    if q:
        c = as_dict(fetch(f'https://www.wikidata.org/wiki/Special:EntityData/{q}.json', 8)
                    ).get('entities', {}).get(q, {}).get('claims', {})
        out = _wd_claims_out(c)
    _wd_c[yt] = (CLOCK.time(), out)
    return out



_WD_SOCIAL = (('P2002', 'X', 'https://x.com/'),
              ('P2003', 'Instagram', 'https://instagram.com/'),
              ('P2013', 'Facebook', 'https://facebook.com/'))



def _wd_country(c):
    """P17/P495 -> label 'en' del país (2ª entidad)."""
    cq = _pv(c, 'P17') or _pv(c, 'P495')
    if not isinstance(cq, dict):
        return None
    d2 = fetch(f'https://www.wikidata.org/wiki/Special:EntityData/{cq["id"]}.json', 6)
    return (as_dict(d2).get('entities', {}).get(cq['id'], {})
            .get('labels', {}).get('en', {}).get('value'))



def _wd_claims_out(c):
    """claims wikidata -> {country, website, year, social[]}."""
    out = {}
    co = _wd_country(c)
    if co:
        out['country'] = co
    web = _pv(c, 'P856')
    if isinstance(web, str):
        out['website'] = web
    inc = _pv(c, 'P571')
    if isinstance(inc, dict) and inc.get('time'):
        out['year'] = inc['time'][1:5]
    for p, label, base in _WD_SOCIAL:
        h = _pv(c, p)
        if isinstance(h, str):
            out.setdefault('social', []).append(
                {'name': label, 'category': 'social', 'url': base + h.lstrip('@')})
    return out
