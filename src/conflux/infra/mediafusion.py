"""MediaFusion (ElfHosted): provisionado lazy de perfil via /encrypt-user-data.

La instancia pública exige config propia (manifest base = 0 catálogos). El
endpoint /encrypt-user-data cifra la config server-side y devuelve un
secret_str → /<secret>/manifest.json — sin cuenta ni email. Cada arranque
re-provisiona (stateless, ~1 POST); la fuente entra como 'MediaFusion',
desactivable en /configure como cualquier otra."""
# pylint: disable=broad-exception-caught
import json
import threading
import urllib.request

from conflux.core.state import CFG, SOURCES, Source, _log, manifests

_HOST = 'https://mediafusion.elfhosted.com'

# Catálogos regionales del manifest de MediaFusion (ids públicos de su
# backend: <idioma>_<calidad> — hdrip/tcrip/old/series/dubbed + live_tv +
# sus catálogos de búsqueda).
_CATS = [
    'tamil_hdrip', 'tamil_dubbed', 'tamil_series',
    'hindi_hdrip', 'hindi_dubbed', 'hindi_series', 'hindi_old',
    'malayalam_hdrip', 'malayalam_series',
    'kannada_hdrip', 'kannada_series',
    'telugu_hdrip', 'telugu_series',
    'bangla_movies', 'punjabi_movies',
    'english_hdrip', 'english_series',
    'live_tv',
    'mediafusion_search_movies', 'mediafusion_search_series',
    'mediafusion_search_tv',
]

_once = False
_lock = threading.Lock()


def _encrypt(cfg: dict) -> str:
    """POST /encrypt-user-data -> secret_str (config cifrada por el servidor)."""
    req = urllib.request.Request(
        _HOST + '/encrypt-user-data',
        data=json.dumps(cfg).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:           # noqa: S310 — host fijo  # nosec B310
        return str(json.load(r).get('encrypted_str') or '')


def _fetch_manifest(base: str):
    from conflux.infra.net import fetch                       # local: evita ciclo import
    d = fetch(base + '/manifest.json', 15)
    return d if isinstance(d, dict) else None


def provision():
    """Si 'mediafusion' está activo y la fuente no existe: cifra config,
    valida el manifest y la añade a SOURCES+manifests (in situ, alineado).
    Se llama en hilo daemon desde load_config — nunca bloquea la carga."""
    global _once                                                   # pylint: disable=global-statement
    with _lock:
        if _once or any(s.name == 'MediaFusion' for s in SOURCES):
            return
    try:
        cfg = {'catalog_configs': [{'catalog_id': c} for c in _CATS]}
        secret = _encrypt(cfg)
        if not secret:
            return
        base = f'{_HOST}/{secret}'
        m = _fetch_manifest(base)
        if not m or not m.get('catalogs'):
            return
        with _lock:
            if any(s.name == 'MediaFusion' for s in SOURCES):
                return
            SOURCES.append(Source('MediaFusion', base))
            manifests.append(m)                  # alineado posicional con SOURCES
            _once = True                         # solo tras éxito: fallos reintentan en /reload
        _log.info('mediafusion: provisionado (%d catálogos)', len(m['catalogs']))
    except Exception as e:
        _log.warning('mediafusion: provision falló (%s)', str(e)[:100])


def maybe_provision():
    """Gate de config + hilo daemon: idempotente, no bloquea load_config."""
    if CFG.get('mediafusion') and not any(s.name == 'MediaFusion' for s in SOURCES):
        threading.Thread(target=provision, daemon=True).start()
