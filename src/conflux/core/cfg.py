"""Config de usuario (config.json + perfiles config.<name>.json)."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import json
import os
import re
import threading

from conflux.core.state import _CFG_DEFAULTS, _CFG_FILE, _DISABLED, CFG, _log, _open


def load_config():
    """Carga config.json sobre defaults; re-deriva _DISABLED y RE_ES."""
    CFG.clear()
    CFG.update(_CFG_DEFAULTS)                  # reset: borrar el archivo = defaults
    try:
        CFG.update({k: v for k, v in json.load(_open(_CFG_FILE)).items() if k in CFG})
    except (OSError, ValueError) as e:
        _log.warning('config.json ilegible (%s) — defaults', e)
    global RE_ES                                                      # pylint: disable=global-statement
    _DISABLED.clear()
    _DISABLED.update(CFG.get('sources_disabled') or [])
    langs = '|'.join(re.escape(x) for x in CFG.get('stream_langs') or ['es'])
    RE_ES = re.compile(rf'\b({langs})\b', re.IGNORECASE)              # langs configurables en vivo

def _save_cfg():
    try:
        _open(_CFG_FILE, 'w').write(json.dumps(
            {k: v for k, v in CFG.items() if v or isinstance(v, list)}, indent=1))
    except OSError:
        pass


load_config()

RE_ES = re.compile(r'\b(es)\b', re.IGNORECASE)

# RE_ES lo re-asigna load_config() desde stream_langs (no estático aquí: el orden
# de módulo es CFG->load_config->regexes; un compile fijo aquí lo pisaría)

# ---- perfiles (2.4): /p/<nombre>/... usa config.<nombre>.json sobre CFG ----
_PROF = threading.local()

_PROFILES = {}

def _profile_load(name):
    if name not in _PROFILES:
        if len(_PROFILES) >= 32:                            # cota: un perfil no visto desaloja al más viejo
            _PROFILES.pop(next(iter(_PROFILES)))
        try:
            _PROFILES[name] = json.load(_open(os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), f'config.{name}.json')))
        except (OSError, ValueError):
            _PROFILES[name] = {}
    return _PROFILES[name]

def _cfgget(k, default=None):
    pc = getattr(_PROF, 'cfg', None)
    if pc is not None and k in pc:
        return pc[k]
    return CFG.get(k, default)

def _eff_disabled():
    pc = getattr(_PROF, 'cfg', None)
    return set(pc['sources_disabled']) if pc is not None and 'sources_disabled' in pc else _DISABLED

def _es_pat():
    pc = getattr(_PROF, 'cfg', None)
    langs = (pc or {}).get('stream_langs') if pc else None
    if not langs:
        return RE_ES.pattern
    return r'\b(' + '|'.join(re.escape(x) for x in langs) + r')\b'
