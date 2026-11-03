"""Config de usuario (config.json + perfiles config.<name>.json)."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import json
import os
import re
import threading

from conflux.core.state import (
    _CFG_DEFAULTS,
    _CFG_FILE,
    _DISABLED,
    _ROOT,
    CFG,
    _atomic_json,
    _log,
    _open,
)


def _coerce(k, v):
    """Normaliza un valor de config.json al tipo de su default — un int guardado
    como str (edición a mano / apply_env viejo) rompería aritmética o iteración."""
    d = _CFG_DEFAULTS.get(k)
    if v is None:
        return d                              # "m3u_path": null -> default, nunca None
    if d is None or isinstance(v, type(d)):
        return v
    try:
        if isinstance(d, bool):
            return v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'sí', 'si')
        if isinstance(d, int):
            return int(v)
        if isinstance(d, str):
            return str(v)
        if isinstance(d, list):
            return list(v) if isinstance(v, (list, tuple)) else [x.strip() for x in str(v).split(',') if x.strip()]
    except (TypeError, ValueError):
        pass
    return d                                                   # irrecuperable → default


_CFG_ENV_REF: dict = {}        # k -> (var, resuelto): 'env:X' re-emite la ref al guardar


def load_config():
    """Carga config.json sobre defaults; re-deriva _DISABLED y RE_ES."""
    CFG.clear()
    CFG.update(_CFG_DEFAULTS)                  # reset: borrar el archivo = defaults
    try:
        raw = json.load(_open(_CFG_FILE))
        unk = sorted(k for k in raw if k not in _CFG_DEFAULTS)
        if unk:                                       # observabilidad: keys huérfanas/stale
            _log.info('config.json: claves desconocidas ignoradas: %s', ', '.join(unk))
        CFG.update({k: _coerce(k, v) for k, v in raw.items() if k in CFG})
    except (OSError, ValueError) as e:
        _log.warning('config.json ilegible (%s) — defaults', e)
    _CFG_ENV_REF.clear()
    for k, v in list(CFG.items()):                     # "env:VAR" -> variable de entorno:
        if isinstance(v, str) and v.startswith('env:'):  # secretos fuera del archivo
            _CFG_ENV_REF[k] = (v[4:], os.environ.get(v[4:], ''))
            CFG[k] = _CFG_ENV_REF[k][1]
    global RE_ES, RE_EX                                             # pylint: disable=global-statement
    _DISABLED.clear()
    _DISABLED.update(CFG.get('sources_disabled') or [])
    langs = '|'.join(re.escape(x) for x in CFG.get('stream_langs') or ['es'])
    RE_ES = re.compile(rf'\b({langs})\b', re.IGNORECASE)              # langs configurables en vivo
    RE_EX = _ex_re(CFG.get('stream_exclude'))


def _ex_re(terms):
    """stream_exclude -> regex. Términos cortos (<=3: 'ts','cam') van con borde
    alfanumérico — 'ts' como substring pura mataría CATS/BOOTS/BTS; los largos
    ('camrip','screener') van substring para enganchar nombres fusionados."""
    parts = []
    for x in terms or []:
        e = re.escape(x)
        parts.append(e if len(x) > 3 else rf'(?<![a-z0-9]){e}(?![a-z0-9])')
    return re.compile(r'(?:' + '|'.join(parts) + ')', re.IGNORECASE) if parts \
        else re.compile(r'(?!)')            # inline: _RE_NEVER aún no existe en init

def _save_cfg():
    try:
        out = {}
        for k, v in CFG.items():
            ref = _CFG_ENV_REF.get(k)
            if ref and v == ref[1]:
                out[k] = 'env:' + ref[0]               # ref intacta: re-emitir, nunca el secreto
            elif v or isinstance(v, (list, bool)):
                out[k] = v                             # bool: stream_prefetch=False debe persistir
        _atomic_json(_CFG_FILE, out)
    except OSError:
        pass


RE_ES = re.compile(r'\b(es)\b', re.IGNORECASE)

_RE_NEVER = re.compile(r'(?!)')                 # sin filtro: nunca matchea
RE_EX = _RE_NEVER                               # stream_exclude vacío = off

# RE_ES/RE_EX los re-asigna load_config() desde stream_langs/stream_exclude:
# load_config() debe ir DESPUÉS de los fallback — si corre antes, el compile
# fijo pisa el patrón derivado de config.json y éste se pierde hasta /reload
load_config()

# ---- perfiles (2.4): /p/<nombre>/... usa config.<nombre>.json sobre CFG ----
_PROF = threading.local()

_PROFILES: dict = {}

_PROFILES_LOCK = threading.Lock()

def _profile_load(name):
    with _PROFILES_LOCK:                                   # pop(next(iter)) + setdefault
        if name not in _PROFILES:                          # concurrentes pisarían la cota
            if len(_PROFILES) >= 32:                        # cota: un perfil no visto desaloja al más viejo
                _PROFILES.pop(next(iter(_PROFILES)))
            try:
                _PROFILES[name] = json.load(_open(os.path.join(
                    _ROOT, f'config.{name}.json')))
            except (OSError, ValueError):
                _PROFILES[name] = {}
        return _PROFILES[name]

def _pcfg():
    """Dict de config del perfil activo en este hilo (None si va el global)."""
    return getattr(_PROF, 'cfg', None)


def _cfgget(k, default=None):
    """Valor efectivo: perfil > global, con guardia de tipos — un override con
    tipo distinto al global se ignora (p.ej. lists como str iteraría chars)."""
    pc = _pcfg()
    if pc is not None and k in pc:
        v, gv = pc[k], CFG.get(k, default)
        if gv is None or isinstance(v, type(gv)) or \
                (isinstance(gv, (int, float)) and isinstance(v, (int, float))):
            return v
        return gv
    return CFG.get(k, default)

def _eff_disabled():
    pc = _pcfg()
    v = _strs(pc['sources_disabled']) if pc is not None and 'sources_disabled' in pc else None
    return set(v) if v is not None else _DISABLED

def _strs(v):
    """Coerce a lista[str]; tipos raros (int, str plano) -> None -> fallback global."""
    if isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
        return list(v)
    return None


def _es_pat():
    """Regex stream_langs efectivo (compiled, igual que _ex_pat)."""
    langs = _strs((_pcfg() or {}).get('stream_langs'))
    if not langs:
        return RE_ES
    return re.compile(r'\b(' + '|'.join(re.escape(x) for x in langs) + r')\b',
                      re.IGNORECASE)


def _ex_pat():
    """Regex stream_exclude efectivo: override de perfil si lo tiene, si no el global."""
    terms = _strs((_pcfg() or {}).get('stream_exclude'))
    return _ex_re(terms) if terms is not None else RE_EX
