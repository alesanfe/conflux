#!/usr/bin/env python3
"""Proxy local para Stremio: unifica el formato de los streams de todos los
addons y pasa sus catálogos/metadatos. Escucha en 127.0.0.1:9700.

La implementación vive en el paquete src/conflux/ — este archivo es el punto
de entrada (`python3 proxy.py`) y la fachada de compatibilidad (tests, scripts)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

# src-layout: los imports de conflux van tras el path insert a propósito
# pylint: disable=wrong-import-position

from conflux.app.stream import handle_stream
from conflux.core.state import CFG, SOURCES, manifests
from conflux.domain.manifest import _UTYPE, build_manifest
from conflux.domain.sources import (
    _idmap,
    _resolve_sid,
    refresh_manifests,
    res_of,
    res_types,
)
from conflux.domain.text import RANK, _ad_link, _id_prefix, _spam, norm_stream, parse_seeds
from conflux.infra.debrid import _db_resolve
from conflux.infra.net import fetch
from conflux.web.server import H, main

__all__ = [
    'CFG',
    'RANK',
    'SOURCES',
    '_UTYPE',
    '_ad_link',
    'H',
    '_db_resolve',
    '_id_prefix',
    '_idmap',
    '_resolve_sid',
    '_spam',
    'build_manifest',
    'fetch',
    'handle_stream',
    'main',
    'manifests',
    'norm_stream',
    'parse_seeds',
    'refresh_manifests',
    'res_of',
    'res_types',
]


if __name__ == '__main__':
    main()
