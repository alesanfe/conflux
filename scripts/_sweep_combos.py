#!/usr/bin/env python3
"""Barrido total: producto cartesiano de TODOS los extras con opciones
(genre x year x ...) por catálogo -> combinaciones que devuelven 0 metas."""
import concurrent.futures as cf
import itertools
import json
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:9700'


def get(path, t=30):
    try:
        with urllib.request.urlopen(f'{BASE}/{path}', timeout=t) as r:
            return json.load(r)
    except Exception:
        return None


def metas(d):
    return len((d or {}).get('metas') or (d or {}).get('metasDetailed') or [])


def probe(job):
    cid, utype, name, extra = job
    e = f"/{urllib.parse.quote(extra, safe='=&,:')}" if extra else ''
    d = get(f'catalog/{utype}/{cid}{e}.json')
    return job, d, metas(d)


mf = get('manifest.json', 25)
jobs = []
for c in mf.get('catalogs', []):
    cid, ut = c['id'], c.get('type', 'movie')
    pickers = [(e['name'], e.get('options') or [])
               for e in (c.get('extra') or []) if e.get('options')]
    jobs.append((cid, ut, c.get('name', ''), None))          # base
    if not pickers:
        continue
    for combo in itertools.product(*[[f'{n}={o}' for o in opts]
                                     for n, opts in pickers]):
        jobs.append((cid, ut, c.get('name', ''), '&'.join(combo)))

print(f'{len(jobs)} combinaciones…', flush=True)
dead, errs = [], []
done = 0
with cf.ThreadPoolExecutor(10) as ex:
    for job, d, n in ex.map(probe, jobs):
        done += 1
        if done % 500 == 0:
            print(f'  {done}/{len(jobs)}', flush=True)
        if d is None:
            errs.append(job)
        elif n == 0:
            dead.append(job)

print(f'\n== VACÍAS ({len(dead)}):')
for cid, ut, name, extra in dead:
    print(f'  {name or cid} [{ut}] {extra or "(base)"}')
print(f'\n== ERRORES HTTP ({len(errs)}):')
for cid, ut, name, extra in errs[:40]:
    print(f'  {name or cid} [{ut}] {extra or "(base)"}')
json.dump({'dead': [list(j) for j in dead], 'err': [list(j) for j in errs]},
          open('_sweep_combos.json', 'w', encoding='utf-8'), ensure_ascii=False)
