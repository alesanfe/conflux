"""Tareas de background: prefetch de catálogos y refresco periódico de manifests."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import threading
import time
from contextlib import suppress

from conflux.core.state import _POOL_BG, log_ev
from conflux.domain.catalog import handle_catalog
from conflux.domain.manifest import build_manifest
from conflux.domain.sources import refresh_manifests
from conflux.infra.epg import epg_kick

_prefetching = threading.Event()

def _prefetch_catalogs():
    """Calienta _cat_c en background: Discover pide ~170 catálogos a la vez y si
    llegan en frío los upstreams lentos hacen saltar 'Load failed' en el cliente."""
    if _prefetching.is_set():
        return
    _prefetching.set()
    try:
        fs = [_POOL_BG.submit(handle_catalog, c['type'], c['id'], None)
              for c in build_manifest()['catalogs']]
        for f in fs:
            with suppress(Exception):
                f.result()
        epg_kick()                          # guía IPTV lista al primer uso
    finally:
        _prefetching.clear()



def _refresher():
    while True:
        time.sleep(1800)
        try:
            refresh_manifests()
        except Exception as e:                                  # noqa: BLE001 — el daemon no debe morir nunca
            log_ev('refresh_err', err=repr(e)[:100])
        try:
            epg_kick()                            # EPG fresco sin esperar petición
        except Exception as e:
            log_ev('epg_err', err=repr(e)[:100])
