#!/usr/bin/env python3
"""Barrido: TODAS las opciones genre/year de TODOS los catálogos del manifest
-> opciones que devuelven 0 metas (candidatas a 'Oops!' en Discover)."""
import concurrent.futures as cf
import json
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:9700'


def get(path, t=30):
    try:
        with urllib.request.urlopen(f'{BASE}/{path}', timeout=t) as r:
            return json.load(r)
    except Exception:
        return {}


def metas(d):
    return len(d.get('metas') or d.get('metasDetailed') or [])


def probe(cid, utype, _name, extra):
    e = f"/{urllib.parse.quote(extra, safe='=&,:')}" if extra else ''
    return metas(get(f'catalog/{utype}/{cid}{e}.json'))


mf = get('manifest.json')
jobs = []                                   # (cid, type, name, extra|None)
for c in mf.get('catalogs', []):
    cid, ut = c['id'], c.get('type', 'movie')
    jobs.append((cid, ut, c.get('name', ''), None))
    for e in c.get('extra') or []:
        if e.get('name') in ('genre', 'year'):
            for o in e.get('options') or []:
                jobs.append((cid, ut, c.get('name', ''),
                             f"{e['name']}={urllib.parse.quote(str(o))}"))

print(f'{len(jobs)} peticiones…')
res = {}                                    # extra|None -> n metas por catálogo
with cf.ThreadPoolExecutor(8) as ex:
    for (cid, ut, name, extra), n in zip(
            jobs, ex.map(lambda j: probe(*j), jobs)):
        res.setdefault((cid, ut, name), {})[extra] = n

dead, alive = [], []
for (cid, ut, name), opts in res.items():
    for extra, n in sorted(opts.items(), key=lambda kv: str(kv[0])):
        (alive if n else dead).append((name or cid, ut, extra or '(base)', n))

print(f'\n== VACÍAS ({len(dead)}/{len(jobs)}):')
for name, ut, extra, _ in dead:
    print(f'  {name} [{ut}] {extra}')
open('_sweep_dead.json', 'w', encoding='utf-8').write(
    json.dumps([{'cat': n, 'type': t, 'extra': e} for n, t, e, _ in dead],
               ensure_ascii=False, indent=1))
