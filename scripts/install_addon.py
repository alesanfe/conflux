#!/usr/bin/env python3
"""Instala/quita el addon del proxy en el perfil de Stremio — automático.

La implementación vive en `conflux.infra.stremio` (stdlib puro, compartida
con el exe frozen); aquí queda solo el CLI.

CLI:  install_addon.py [--kill]    instala (linux: --kill cierra Stremio)
      install_addon.py --remove    lo quita de la colección
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

# re-export público: installer.py y tests/e2e importan este módulo
from conflux.infra.stremio import (  # noqa: E402
    PROXY_MANIFEST,
    find_authkey,
    find_db,
    install,
    install_remote,
    remove,
    remove_remote,
)

__all__ = ['PROXY_MANIFEST', 'find_authkey', 'find_db', 'install',
           'install_remote', 'remove', 'remove_remote']


def ensure_stremio_cerrado():
    """Solo flatpak/linux: la app abierta persistiría su estado encima (R6)."""
    if subprocess.run(['pgrep', '-f', 'com.stremio.Stremio'],
                      check=False, capture_output=True).returncode != 0:
        return
    if '--kill' not in sys.argv:
        sys.exit('Stremio está abierto — ciérralo, o reejecuta con --kill '
                 '(flatpak kill com.stremio.Stremio)')
    print('cerrando Stremio (flatpak kill)…')
    subprocess.run(['flatpak', 'kill', 'com.stremio.Stremio'],
                   check=False, capture_output=True)
    for _ in range(50):
        if subprocess.run(['pgrep', '-f', 'com.stremio.Stremio'],
                          check=False, capture_output=True).returncode != 0:
            return
        time.sleep(.2)
    sys.exit('Stremio no terminó de cerrarse')


def main():
    url = next((a for a in sys.argv[1:] if a.startswith('http')),
               PROXY_MANIFEST)
    if '--remove' in sys.argv:
        st, msg = remove(url)
        print(f'{st}: {msg}')
        return
    if find_db():                      # escritura local -> exige app cerrada
        ensure_stremio_cerrado()
    st, msg = install(url)
    print(f'{st}: {msg}')
    if st == 'manual':
        print('Sin sesión — instala con el deep-link:')
        print(f'  stremio://{url.split("//", 1)[1]}')


if __name__ == '__main__':
    main()
