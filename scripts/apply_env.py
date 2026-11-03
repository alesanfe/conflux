#!/usr/bin/env python3
"""apply_env.py — vuelca el `.env` del repo a config.json y sources.json.

Uso:  python scripts/apply_env.py [archivo.env]   (por defecto `.env` en la raíz)

Formato `.env`: `CLAVE=valor` por línea, `#` comentarios. Valores vacíos no
pisan lo que haya. Idempotente: se puede correr cuando se quiera (tras editar
.env hay que hacer `POST /reload` o reiniciar el proxy).

SOURCES_EXTRA="Nombre|url;Otro|url2" añade o reemplaza entradas en sources.json
(para fuentes cuya URL lleva token: MediaFusion, Comet, AIOStreams, Orion…).
Escrituras siempre atómicas (tmp+rename) como el resto del proyecto.
"""
import ipaddress
import json
import os
import socket
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _public_url(url):
    """Mismo guard SSRF que infra/net.py (copia: este script corre antes del
    venv, sin imports de conflux). http(s) + nada de link-local/metadata."""
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname:
            return False
        port = p.port or (443 if p.scheme == 'https' else 80)
        infos = socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP)
        return bool(infos) and all(
            not (ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)
            for ip in (ipaddress.ip_address(i[4][0]) for i in infos))
    except (OSError, ValueError):
        return False

ENV2CFG = {
    'DEBRID_PROVIDER': 'debrid_provider',       # realdebrid|torbox|alldebrid|premiumize|easydebrid|debridlink
    'DEBRID_KEY': 'debrid_key',
    'TRAKT_CLIENT_ID': 'trakt_client_id',
    'TRAKT_CLIENT_SECRET': 'trakt_client_secret',
    'OPENSUBTITLES_KEY': 'opensubtitles_key',
    'OPENSUBTITLES_USER': 'opensubtitles_user',
    'OPENSUBTITLES_PASS': 'opensubtitles_pass',
    'MDBLIST_KEY': 'mdblist_key',
    'CATALOG_LISTS': 'catalog_lists',
    'M3U_PATH': 'm3u_path',                   # M3U_URL descarga la lista a m3u/ y apunta ahí
    'EPG_URL': 'epg_url',                     # XMLTV(.gz) con la parrilla — guía nativa
    'TORZNAB_URL': 'torznab_url',
    'TORZNAB_KEY': 'torznab_key',
    'ZILEAN_URL': 'zilean_url',
    'ADMIN_TOKEN': 'admin_token',
    'TLS_CERT': 'tls_cert',
    'TLS_KEY': 'tls_key',
    'CALENDAR_MONTHS_AHEAD': 'calendar_months_ahead',
    'SOURCES_DISABLED': 'sources_disabled',
    'CATALOGS_DISABLED': 'catalogs_disabled',
    'SOURCE_ORDER': 'source_order',
    'STREAM_LANGS': 'stream_langs',
    'STREAM_EXCLUDE': 'stream_exclude',
    'RPDB_KEY': 'rpdb_key',
}
LIST_KEYS = {'sources_disabled', 'catalogs_disabled', 'source_order', 'stream_langs',
             'stream_exclude'}
INT_KEYS = {'calendar_months_ahead', 'trakt_expires'}


def _atomic_json(path, data):
    """tmp+rename en el mismo dir: un corte a mitad de write no corrompe."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _read_env(path):
    env = {}
    with open(path, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('#') or '=' not in ln:
                continue
            k, v = ln.split('=', 1)
            env[k.strip().upper()] = v.strip().strip('"').strip("'")
    return env


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main():
    env_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, '.env')
    if not os.path.exists(env_path):
        print(f'sin {env_path} — copia .env.example y rellena tus claves')
        return 0
    env = _read_env(env_path)

    cfg_path = os.path.join(ROOT, 'config.json')
    cfg = _load_json(cfg_path, {})
    n = 0
    for k, v in env.items():
        ck = ENV2CFG.get(k)
        if not ck or v == '':
            continue
        if ck in LIST_KEYS:
            cfg[ck] = [x.strip() for x in v.split(',') if x.strip()]
        elif ck in INT_KEYS:
            cfg[ck] = int(v)
        else:
            cfg[ck] = v
        n += 1
    if n or not os.path.exists(cfg_path):
        _atomic_json(cfg_path, cfg)

    extra = env.get('SOURCES_EXTRA', '')
    if extra:
        src_path = os.path.join(ROOT, 'sources.json')
        srcs = _load_json(src_path, _load_json(os.path.join(ROOT, 'sources.example.json'), []))
        for item in extra.split(';'):
            if '|' not in item:
                continue
            name, base = (x.strip() for x in item.split('|', 1))
            if not name or not base:
                continue
            for s in srcs:
                if s['name'].lower() == name.lower():
                    s['base'] = base
                    break
            else:
                srcs.append({'name': name, 'base': base})
        _atomic_json(src_path, srcs)
        print(f'sources.json: {len(srcs)} fuentes')

    m3u_url = env.get('M3U_URL', '')
    if m3u_url and not _public_url(m3u_url):
        print(f'm3u: {m3u_url} rechazada (scheme o destino no permitido)')
        m3u_url = ''
    if m3u_url:
        import urllib.request
        os.makedirs(os.path.join(ROOT, 'm3u'), exist_ok=True)
        name = os.path.basename(urllib.request.urlparse(m3u_url).path) or 'list.m3u'
        dst = os.path.join(ROOT, 'm3u', name)
        try:
            urllib.request.urlretrieve(m3u_url, dst)
            cfg['m3u_path'] = dst
            _atomic_json(cfg_path, cfg)
            print(f'm3u: {m3u_url} -> {dst}')
        except OSError as e:
            print(f'm3u: descarga fallida ({e})')

    print(f'config.json: {n} claves aplicadas')
    return 0


if __name__ == '__main__':
    sys.exit(main())
