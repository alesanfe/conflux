#!/usr/bin/env python3
"""Graba respuestas upstream reales -> tests/fixtures/e2e_responses.json.
La e2e las replaya en modo hermético (CI determinista); regenerar con:
    python3 scripts/record_e2e.py
URLs normalizadas (años -> {Y}) para que el fixture siga sirviendo entre años."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, ROOT)

import proxy as px
from tests import patch_fetch  # (tests/__init__ pone sys.path)

from conflux.app.meta import handle_meta
from conflux.domain.catalog import handle_catalog

REC = {}


def _norm(url):
    """Años/ids volátiles -> marcador, para replay estable entre fechas."""
    return re.sub(r'(?<!\d)(?:19|20)\d{2}(?!\d)', '{Y}', url)


def _rec(url, timeout=None):
    d = _REAL(url, timeout)
    if d is not None:
        REC[_norm(url)] = d
    return d


def main():
    global _REAL                                    # pylint: disable=global-variable-undefined
    from conflux.infra import net
    _REAL = net.fetch

    undo = patch_fetch(_rec)
    try:
        px.refresh_manifests()
        px.build_manifest()
        # los mismos flujos que la e2e ejercita:
        for st_, sid in (('movie', 'tt0137523'), ('series', 'tt0944947:1:1'),
                         ('series', 'kitsu:49847:2'), ('movie', 'zzzzzz')):
            px.handle_stream(st_, sid)
        for st_, sid in (('movie', 'tt0137523'), ('series', 'tt0944947'),
                         ('series', 'mx:6429078990673560656:kagewani-gG7FoWufVE7')):
            handle_meta(st_, sid)
        for utype, cid, extra in (
            ('movie', 'scinemeta_movie_estrenos', None),
            ('movie', 'scinemeta_movie_top', None),
            ('series', 'scinemeta_series_top', None),
            ('movie', 'suniform_movie_search', 'search=dune'),
            ('series', 'scinemeta_series_estrenoscal', 'calendarVideosIds=tt0944947'),
            ('series', 'scinemeta_series_estrenoscal', 'calendarVideosIds=kitsu:49847'),
        ):
            handle_catalog(utype, cid, extra)
    finally:
        undo()

    out = os.path.join(ROOT, 'tests', 'fixtures', 'e2e_responses.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(REC, f, separators=(',', ':'), ensure_ascii=False)
    print(f'{len(REC)} urls grabadas -> {out} ({os.path.getsize(out)//1024} KB)')


if __name__ == '__main__':
    main()
