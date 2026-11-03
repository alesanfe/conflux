"""Tareas de background: prefetch de catálogos y refresco periódico de manifests."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress

from conflux.core.state import _POOL_BG, _ROOT, CFG, log_ev
from conflux.domain.catalog import handle_catalog
from conflux.domain.manifest import build_manifest
from conflux.domain.sources import refresh_manifests
from conflux.infra.epg import epg_kick

_prefetching = threading.Event()

_SYNC_SCRIPT = os.path.join(_ROOT, 'scripts', 'trakt_sync.py')


def _trakt_sync():
    """Auto-scrobble: empuja lo visto en Stremio -> Trakt (idempotente, subproceso
    aislado — si el leveldb está bloqueado por la app simplemente reintenta en el
    siguiente ciclo)."""
    if not CFG.get('trakt_access_token') or not os.path.exists(_SYNC_SCRIPT):
        return
    try:
        r = subprocess.run([sys.executable, _SYNC_SCRIPT], capture_output=True,
                           timeout=300, text=True, check=False)
        log_ev('trakt_sync', rc=r.returncode, out=(r.stdout or r.stderr or '').strip()[:200])
    except Exception as e:                                        # noqa: BLE001 — el daemon no debe morir
        log_ev('trakt_sync_err', err=repr(e)[:100])

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
        _trakt_sync()                       # historial Stremio -> Trakt al arrancar
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
        try:
            _trakt_sync()                         # scrobble visto -> Trakt cada 30min
        except Exception as e:
            log_ev('trakt_sync_err', err=repr(e)[:100])
