"""Tareas de background: prefetch de catálogos y refresco periódico de manifests."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import sys
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



def _mem_trim():
    """Devuelve memoria al SO tras los picos de collect: los buffers de
    upstreams (M3U/EPG de decenas de MB) dejan arenas reservadas que
    Python no suelta por sí solo — gc cierra ciclos y malloc_trim pide a
    libc que descomita las páginas libres. En Windows gc.collect() basta
    para que el heap ucrt pueda decomitar segmentos."""
    import ctypes
    import gc
    gc.collect()
    if sys.platform.startswith('linux'):
        with suppress(Exception):
            ctypes.CDLL('libc.so.6').malloc_trim(0)


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
        try:
            _mem_trim()                           # piso de memoria bajo control
        except Exception as e:
            log_ev('memtrim_err', err=repr(e)[:100])
