"""Registro del addon en el perfil de Stremio — automático.

Modelo (R6): la colección REMOTA (api.strem.io) es la autoridad — con sesión
iniciada pisa el perfil local al arrancar. La vía principal es por tanto la API
remota con el authKey extraído del almacenamiento local de Stremio:

- Windows Stremio 4 (QtWebEngine):
    %LOCALAPPDATA%\\Smart Code ltd\\Stremio\\QtWebEngine\\Default\\Local Storage\\leveldb
- Windows/macOS/Linux Stremio 5 (Electron) y Stremio 4 nativo:
    <user-data>/**/Local Storage/leveldb
- Linux flatpak (sqlite, BLOB UTF-16LE):
    ~/.var/app/com.stremio.Stremio/**/LocalStorage/localstorage.sqlite3
    — además del remoto se escribe el perfil local para que aplique al instante.

Sin authKey (Stremio nunca abierto / perfil vacío) -> 'manual': el llamador
debe caer al deep-link stremio://<manifest>.

Vive en el paquete (stdlib puro) para que el exe frozen y scripts/install_addon.py
compartan la misma implementación.
"""
import glob
import json
import os
import re
import sqlite3
import urllib.request

PROXY_MANIFEST = 'http://127.0.0.1:9700/manifest.json'
API = 'https://api.strem.io/api'
DB_GLOB = os.path.expanduser(
    '~/.var/app/com.stremio.Stremio/data/stremio/storage/'
    '**/LocalStorage/localstorage.sqlite3')
# 'authKey' en leveldb: clave + relleno binario + "valor" (base64-ish)
_KEY_RE = re.compile(rb'uthKey.{0,15}?"([A-Za-z0-9+/=_\-]{20,200})"', re.S)
_VALID_RE = re.compile(r'^[A-Za-z0-9+/=_\-]{20,200}$')
# Límites del scraping local (mínimo privilegio, nada de barridos sin cota):
_MAX_FILE_BYTES = 64 * 1024 * 1024    # un .log/.ldb de leveldb no pasa de ~4 MiB
_MAX_FILES = 40                        # por directorio, solo los más recientes
_SCRAPE_BUDGET_S = 5.0                 # tiempo total máximo de escaneo
_MAX_DB_CANDIDATES = 20                # sqlite: candidatas a inspeccionar


# ---------- localización del authKey ----------

def _leveldb_dirs():
    """Candidatas <profile>/Local Storage/leveldb según SO y versión."""
    home = os.path.expanduser('~')
    la = os.environ.get('LOCALAPPDATA', os.path.join(home, 'AppData', 'Local'))
    ra = os.environ.get('APPDATA', os.path.join(home, 'AppData', 'Roaming'))
    bases = [
        os.path.join(la, 'Smart Code ltd', 'Stremio'),          # win stremio4
        os.path.join(ra, 'stremio'),                            # win stremio5
        os.path.join(ra, 'stremio5'),
        os.path.join(la, 'stremio'),
        os.path.join(home, 'Library', 'Application Support',    # mac
                     'Smart Code ltd', 'Stremio'),
        os.path.join(home, 'Library', 'Application Support', 'stremio'),
        os.path.join(home, 'Library', 'Application Support', 'stremio5'),
        os.path.join(home, '.local', 'share', 'Smart Code ltd', 'Stremio'),
        os.path.join(home, '.config', 'stremio'),
        os.path.join(home, '.config', 'stremio5'),
    ]
    out = []
    for b in bases:
        out += glob.glob(os.path.join(b, '**', 'Local Storage', 'leveldb'),
                         recursive=True)
        out += glob.glob(os.path.join(b, 'leveldb'))
    return [d for d in out if os.path.isdir(d)]


def _valid_key(k):
    return bool(k) and bool(_VALID_RE.match(k))


def _authkey_leveldb():
    """Scraping acotado: solo leveldb conocido, sin symlinks, N ficheros
    recientes por dir, tope de bytes y presupuesto de tiempo total."""
    from conflux.core.state import CLOCK
    t0, files = CLOCK.time(), []
    for d in _leveldb_dirs():
        try:
            fs = [f for f in (glob.glob(os.path.join(d, '*.log'))
                              + glob.glob(os.path.join(d, '*.ldb')))
                  if not os.path.islink(f)]
            fs.sort(key=os.path.getmtime, reverse=True)
            files += fs[:_MAX_FILES]
        except OSError:
            continue
    for f in sorted(files, key=lambda p: os.path.getmtime(p), reverse=True):
        if CLOCK.time() - t0 > _SCRAPE_BUDGET_S:
            break
        try:
            if os.path.getsize(f) > _MAX_FILE_BYTES:
                continue
            with open(f, 'rb') as fh:
                hits = _KEY_RE.findall(fh.read(_MAX_FILE_BYTES + 1))
        except OSError:
            continue
        for h in reversed(hits):        # el último match es el más reciente
            k = h.decode('ascii', 'ignore')
            if _valid_key(k):
                return k
    return None


def find_db():
    cands = sorted(glob.glob(DB_GLOB, recursive=True),
                   key=os.path.getmtime, reverse=True)[:_MAX_DB_CANDIDATES]
    for p in cands:
        c = None
        try:
            c = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
            if c.execute("SELECT 1 FROM ItemTable WHERE key='profile'").fetchone():
                return p
        except sqlite3.Error:
            pass
        finally:
            if c is not None:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
    return None


def _profile_sqlite(db):
    c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        return json.loads(c.execute(
            "SELECT value FROM ItemTable WHERE key='profile'")
            .fetchone()[0].decode('utf-16-le'))
    finally:
        c.close()


def _authkey_sqlite():
    db = find_db()
    if not db:
        return None
    try:
        k = (_profile_sqlite(db).get('auth') or {}).get('key')
        return k if _valid_key(k) else None
    except Exception:
        return None


# ---------- CredentialProvider chain ----------

def _key_env():
    return os.environ.get('CONFLUX_STREMIO_AUTHKEY') or None


def _key_cfg():
    try:
        from conflux.core.cfg import _cfgget
        return _cfgget('stremio_authkey') or None
    except Exception:
        return None


def _key_keyring():
    try:
        import keyring  # pylint: disable=import-error  # opcional
        return keyring.get_password('conflux', 'stremio_authkey')
    except Exception:
        return None


def _scrape_enabled():
    """El scraping de ficheros locales puede desactivarse:
    config `authkey_scrape: false` o env CONFLUX_STREMIO_SCRAPE=0."""
    try:
        from conflux.core.cfg import _cfgget
        if not _cfgget('authkey_scrape', True):
            return False
    except Exception:
        pass
    return os.environ.get('CONFLUX_STREMIO_SCRAPE', '1').lower() \
        not in ('0', 'false', 'no', 'off')


# (nombre, atributo) — resueltos en llamada para que tests/parches lleguen.
_PROVIDERS = (('env', '_key_env'), ('config', '_key_cfg'),
              ('keyring', '_key_keyring'),
              ('leveldb', '_authkey_leveldb'), ('sqlite', '_authkey_sqlite'))


def find_auth():
    """Devuelve (authKey, proveedor). Orden: explícito > seguro > scraping."""
    for name, attr in _PROVIDERS:
        if name in ('leveldb', 'sqlite') and not _scrape_enabled():
            continue
        try:
            k = globals()[attr]()
        except Exception:
            continue
        if _valid_key(k):
            return k, name
    return None, None


def find_authkey():
    return find_auth()[0]


# ---------- api.strem.io ----------

def _api(method, payload):
    req = urllib.request.Request(
        f'{API}/{method}', data=json.dumps(payload).encode(),
        headers={'content-type': 'application/json'})
    res = json.load(urllib.request.urlopen(req, timeout=15))  # nosec B310
    if res.get('error'):
        raise RuntimeError(str(res['error']))
    return res.get('result')


def _api_get(key):
    return (_api('addonCollectionGet', {'authKey': key, 'collection': {}})
            or {}).get('addons', [])


def _api_set(key, addons):
    _api('addonCollectionSet', {'authKey': key, 'addons': addons})


# ---------- alto nivel ----------

def _safe_err(e, key):
    """Mensaje de error sin credencial: el servidor podría hacer eco del
    payload (incluido authKey) en su propio texto de error."""
    s = str(e)
    return s.replace(key, '***') if key else s


def install_remote(manifest_url=PROXY_MANIFEST):
    """Devuelve (estado, detalle): installed | already | manual | error."""
    key, prov = find_auth()
    if not key:
        return 'manual', 'sin authKey — Stremio sin abrir o sin sesión'
    try:
        addons = _api_get(key)
        man = json.load(urllib.request.urlopen(manifest_url, timeout=10))  # nosec B310
        desc = {'manifest': man, 'transportUrl': manifest_url,
                'flags': {'official': False, 'protected': False}}
        for i, a in enumerate(addons):
            if a.get('transportUrl') == manifest_url:
                if a.get('manifest') == man:
                    return 'already', f'{len(addons)} addons en colección'
                addons[i] = desc            # snapshot viejo -> el selector mostraría
                _api_set(key, addons)       # catálogos borrados (R6: remoto manda)
                return 'updated', f'manifest refrescado ({len(addons)} addons)'
        addons.append(desc)
        _api_set(key, addons)
        return 'installed', f'{len(addons)} addons (remoto, {prov})'
    except Exception as e:
        return 'error', _safe_err(e, key)


def remove_remote(manifest_url=PROXY_MANIFEST):
    key, _prov = find_auth()
    if not key:
        return 'manual', 'sin authKey'
    try:
        addons = _api_get(key)
        new = [a for a in addons if a.get('transportUrl') != manifest_url]
        if len(new) == len(addons):
            return 'absent', ''
        _api_set(key, new)
        return 'removed', f'{len(new)} addons'
    except Exception as e:
        return 'error', _safe_err(e, key)


def _sqlite_update(fn):
    """fn(addons) -> lista nueva; escribe el perfil (BLOB UTF-16LE)."""
    db = find_db()
    if not db:
        return False
    c = sqlite3.connect(db)
    try:
        prof = json.loads(c.execute(
            "SELECT value FROM ItemTable WHERE key='profile'")
            .fetchone()[0].decode('utf-16-le'))
        addons = prof.setdefault('addons', [])
        addons[:] = fn(addons)
        blob = json.dumps(prof).encode('utf-16-le')  # nunca TEXT UTF-8
        c.execute("UPDATE ItemTable SET value=? WHERE key='profile'", (blob,))
        c.commit()
        return True
    finally:
        c.close()


def install(manifest_url=PROXY_MANIFEST):
    """Remoto si hay sesión; además perfil local en flatpak (aplica al instante)."""
    st, msg = install_remote(manifest_url)
    local = False
    if find_db():
        man = json.load(urllib.request.urlopen(manifest_url, timeout=10))  # nosec B310
        desc = {'manifest': man, 'transportUrl': manifest_url,
                'flags': {'official': False, 'protected': False}}
        local = _sqlite_update(
            lambda ads: [a for a in ads
                         if a.get('transportUrl') != manifest_url] + [desc])
    if st in ('installed', 'updated'):
        return st, msg + (' + perfil local' if local else '')
    if st == 'already':
        return 'already', msg + (' (local sync)' if local else '')
    if local:
        return 'installed', 'perfil local (sin sesión remota)'
    return st, msg


def remove(manifest_url=PROXY_MANIFEST):
    st, msg = remove_remote(manifest_url)
    try:
        _sqlite_update(lambda ads: [a for a in ads
                                    if a.get('transportUrl') != manifest_url])
    except Exception:
        pass
    return st, msg
