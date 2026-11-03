#!/usr/bin/env python3
"""trakt_auth.py — login Trakt en 1 paso (device flow).

Abre auth.trakt.tv/activate en el navegador con el código ya generado por
el proxy local; el usuario solo inicia sesión (cuenta gratuita) y el token
se guarda solo en config.json vía el poll en background de conflux.

Uso:  python scripts/trakt_auth.py          # abre navegador y espera
      python scripts/trakt_auth.py --no-open  # solo imprime el código
"""
import json
import os
import re
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = 'http://127.0.0.1:9700'


def _cfg():
    return json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))


def main():
    cfg = _cfg()
    if not cfg.get('trakt_client_id') or not cfg.get('trakt_client_secret'):
        sys.exit('faltan TRAKT_CLIENT_ID/SECRET en .env — corre apply_env.py')
    if cfg.get('trakt_access_token'):
        print('trakt ya conectado — nada que hacer')
        return 0
    # /auth/trakt arranca el device flow y devuelve la página con el código
    html = urllib.request.urlopen(f'{PROXY}/auth/trakt', timeout=15).read().decode()
    code = (re.search(r'<b>([A-Z0-9]{6,10})</b>', html) or
            re.search(r'code[^A-Z0-9]{0,12}([A-Z0-9]{6,10})', html))
    url = re.search(r'https://auth\.trakt\.tv/activate[^\s<"]*', html)
    if not code:
        sys.exit('no se pudo obtener user_code — ¿está el proxy corriendo?')
    print(f'código: {code.group(1)}')
    print(f'entra en {url.group(0) if url else "https://auth.trakt.tv/activate"} '
          'e inicia sesión con tu cuenta trakt.tv')
    if '--no-open' not in sys.argv:
        webbrowser.open(url.group(0) if url else 'https://auth.trakt.tv/activate')
    # espera a que el poll del proxy guarde el token (ventana 10 min)
    for _ in range(120):
        time.sleep(5)
        if _cfg().get('trakt_access_token'):
            print('trakt conectado — token guardado en config.json')
            return 0
    print('expiró la ventana — vuelve a correr el script')
    return 1


if __name__ == '__main__':
    sys.exit(main())
