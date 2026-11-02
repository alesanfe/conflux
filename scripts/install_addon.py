#!/usr/bin/env python3
"""Instala el addon del proxy en el perfil de Stremio (flatpak).

- Inserta/actualiza el descriptor http://127.0.0.1:9700/manifest.json en la
  colección de addons del perfil localStorage (BLOB UTF-16LE — escribir TEXT
  UTF-8 rompe la app: "expected value at line 1 column 1").
- Si la sesión está iniciada, sincroniza la colección REMOTA (api.strem.io),
  que si no, pisa el perfil local al arrancar.

Requisito: Stremio COMPLETAMENTE cerrado (si corre, persistirá su estado en
memoria encima de este cambio al salir).
"""
import glob, json, os, sqlite3, sys, urllib.request

PROXY_MANIFEST = 'http://127.0.0.1:9700/manifest.json'
DB_GLOB = os.path.expanduser(
    '~/.var/app/com.stremio.Stremio/data/stremio/storage/'
    '**/LocalStorage/localstorage.sqlite3')


def find_db():
    for p in sorted(glob.glob(DB_GLOB, recursive=True), key=os.path.getmtime, reverse=True):
        try:
            c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
            if c.execute("SELECT 1 FROM ItemTable WHERE key='profile'").fetchone():
                c.close(); return p
            c.close()
        except sqlite3.Error:
            pass
    return None


def main():
    db = find_db()
    if not db:
        sys.exit('No se encontró localstorage.sqlite3 — ¿Stremio instalado y abierto alguna vez?')

    man = json.load(urllib.request.urlopen(PROXY_MANIFEST, timeout=10))
    desc = {'manifest': man, 'transportUrl': PROXY_MANIFEST,
            'flags': {'official': False, 'protected': False}}

    c = sqlite3.connect(db)
    prof = json.loads(c.execute(
        "SELECT value FROM ItemTable WHERE key='profile'").fetchone()[0]
        .decode('utf-16-le'))
    addons = prof.setdefault('addons', [])
    addons[:] = [a for a in addons if a.get('transportUrl') != PROXY_MANIFEST]
    addons.append(desc)
    blob = json.dumps(prof).encode('utf-16-le')           # BLOB UTF-16LE, nunca TEXT
    c.execute("UPDATE ItemTable SET value=? WHERE key='profile'", (blob,))
    c.commit()
    print(f'local OK: {len(addons)} addons en {db}')

    auth = prof.get('auth') or {}
    key = auth.get('key')
    if not key:
        print('sin sesión remota (no logueado): listo — arranca Stremio')
        return
    req = urllib.request.Request(
        'https://api.strem.io/api/addonCollectionSet',
        data=json.dumps({'authKey': key, 'addons': addons}).encode(),
        headers={'content-type': 'application/json'})
    res = json.load(urllib.request.urlopen(req, timeout=15))
    print('remoto:', res.get('result') or res)
    print('Listo — arranca Stremio y comprueba que aparece el addon del proxy.')


if __name__ == '__main__':
    main()
