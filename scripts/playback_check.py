#!/usr/bin/env python3
"""Playback check real: conflux /stream -> infoHash -> stremio-server sirve
bytes de vídeo por HTTP Range (exactamente lo que consume el player de
Stremio).

Contrato de fallo: exit 1 si
- conflux no responde o /stream no devuelve streams con infoHash
- stremio-server (127.0.0.1:11470) no devuelve 200/206 para el infoHash
- los primeros bytes no son un contenedor de vídeo (ftyp/EBML/RIFF-AVI)
- un seek a mitad de fichero no devuelve datos reales

Requiere:
- conflux sirviendo en 127.0.0.1:9700 (o CONFLUX_URL)
- stremio-server/streaming corriendo en 127.0.0.1:11470 (o STREMIO_URL)

Uso: python scripts/playback_check.py [movie|series]  (default: movie)
"""
import json
import os
import sys
import urllib.request

if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CONFLUX = os.environ.get('CONFLUX_URL', 'http://127.0.0.1:9700')
STREMIO = os.environ.get('STREMIO_URL', 'http://127.0.0.1:11470')

MOVIE_ID = 'tt0137523'            # Fight Club — catálogo/streams densos
SERIES_ID = 'tt0944947%3A1%3A1'   # GoT S01E01


def _get(url, timeout=30, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b''
        return e.code, dict(e.headers), body


def _pick_stream(kind):
    code, _h, body = _get(f'{CONFLUX}/stream/{kind}/'
                          f'{MOVIE_ID if kind == "movie" else SERIES_ID}.json',
                          timeout=90)
    assert code == 200, f'/stream -> {code}'
    streams = json.loads(body)['streams']
    cand = [s for s in streams if s.get('infoHash')]
    assert cand, 'sin streams con infoHash'
    # el título suele llevar "N seeds" — el de más seeds resuelve más rápido
    import re
    def _seeds(s):
        m = re.search(r'(\d+)', (s.get('title') or '') + (s.get('name') or ''))
        return int(m.group(1)) if m else 0
    cand.sort(key=_seeds, reverse=True)
    return cand[0]


def _is_video_container(b):
    return (b[:4] == b'\x1a\x45\xdf\xa3'            # EBML (mkv/webm)
            or b[4:8] == b'ftyp'                   # mp4/mov
            or (b[:4] == b'RIFF' and b[8:12] == b'AVI '))


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else 'movie'
    fails = []

    code, _h, _b = _get(f'{CONFLUX}/health/live', timeout=10)
    if code != 200:
        sys.exit(f'precondición: conflux no responde ({code})')
    code, _h, _b = _get(f'{STREMIO}/hlsv2/echo', timeout=5)
    if code != 200:
        code, _h, _b = _get(f'{STREMIO}/settings', timeout=5)
    if code != 200:
        sys.exit('precondición: stremio-server no responde en :11470 '
                 '(¿Stremio abierto?)')

    stream = _pick_stream(kind)
    ih = stream['infoHash']
    print(f'stream elegido: {ih[:16]}… ({(stream.get("name") or "?")})')

    code, hdrs, head = _get(f'{STREMIO}/{ih}/0', timeout=120,
                            headers={'Range': 'bytes=0-1023'})
    if code not in (200, 206):
        fails.append(f'stremio-server /{ih[:8]}/0 -> {code}')
    else:
        size = hdrs.get('Content-Range', '').split('/')[-1]
        ctype = hdrs.get('Content-Type', '')
        print(f'head: {code} {ctype} size={size}B')
        if not _is_video_container(head):
            fails.append(f'primeros bytes no son contenedor vídeo '
                         f'({head[:8].hex()})')
        if 'video' not in ctype:
            fails.append(f'Content-Type no vídeo: {ctype}')

        # seek a mitad: los players hacen acceso aleatorio
        if size.isdigit():
            mid = int(size) // 2
            code2, _h2, mid_b = _get(
                f'{STREMIO}/{ih}/0', timeout=120,
                headers={'Range': f'bytes={mid}-{mid + 511}'})
            if code2 != 206 or len(mid_b) < 512:
                fails.append(f'seek mid-file -> {code2} ({len(mid_b)}B)')
            elif not any(mid_b[:256]):
                fails.append('seek mid-file devolvió ceros')

    if fails:
        print('PLAYBACK FALLO:')
        for f in fails:
            print(' -', f)
        return 1
    print('PLAYBACK OK — stremio-server sirve vídeo real por BT desde el '
          'infoHash de conflux (header + seek mid-file)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
