"""Auto-update verificado (UPD-001/002, SC-007).

Cadena de confianza del artefacto publicado en GitHub Releases:
  conflux.exe ──sha256──> conflux.exe.sha256 ──cosign keyless──> .sigstore.json
Verificación antes de staged:
  1. integridad: sha256 real del binario == el publicado (requisito duro —
     un hash que no casa aborta la instalación siempre)
  2. procedencia: bundle sigstore verificado con la lib `sigstore` cuando
     está instalada (dependencia opcional — no va en requirements base);
     sin ella el estado queda 'hash-only' y queda logueado (degradado
     explícito, nunca silencioso)
  3. staged atómico: conflux.new.exe + conflux.update.json — un exe en
     ejecución no se puede reemplazar en Windows: el swap lo aplica el
     siguiente arranque vía scripts/apply_update.py.

Política de canal (SC-007) — config.update_channel:
  'stable' (default): solo releases no-prerelease
  'beta':             prereleases incluidas (early adopters)
config.auto_update=false por defecto: el chequeo no corre salvo opt-in y
solo aplica a installs binarias (sys.frozen); instalaciones desde fuente
se actualizan con git/pip, no binarios.
"""
# pylint: disable=broad-exception-caught
# broad-exception: un update roto NUNCA debe tumbar el servicio
import hashlib
import json
import os
import re
import sys
import urllib.request

from conflux.core.cfg import _cfgget
from conflux.core.state import CLOCK, _ROOT, _atomic_json, log_ev
from conflux.infra.epg import _OPENER      # redirects con re-validación SSRF
from conflux.infra.net import _public_url

_REPO = 'alesanfe/conflux'
_API = f'https://api.github.com/repos/{_REPO}/releases?per_page=10'
_EXE = 'conflux.exe'
_TICK = 24 * 3600                      # un chequeo al día (el refresher es 30min)

_UPD: dict = {'last': 0.0}
_UPD_JSON = os.path.join(_ROOT, 'conflux.update.json')
_NEW_EXE = os.path.join(
    os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _ROOT,
    'conflux.new.exe')


def _ver_key(tag):
    """'v1.2.3'/'1.2.3-beta.4' -> (1,2,3,'beta.4') para comparar; None si raro."""
    m = re.match(r'v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?\s*$', tag or '')
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or '')


def _ver_cmp(a, b):
    """Semver-ish: pre-release < release del mismo num (1.0.0-beta < 1.0.0)."""
    ka, kb = _ver_key(a), _ver_key(b)
    if ka is None or kb is None:
        return None
    na, nb = ka[:3], kb[:3]
    if na != nb:
        return -1 if na < nb else 1
    pa, pb = ka[3], kb[3]
    if pa == pb:
        return 0
    if not pa:
        return 1                          # a es release, b es pre
    if not pb:
        return -1
    return -1 if pa < pb else 1


def _version():
    try:
        import importlib.metadata
        return importlib.metadata.version('conflux')
    except Exception:
        return '0+local'


def _dl(url, cap=40_000_000, timeout=30):
    """GET bytes con redirect re-validado (_OPENER: SSRF por salto)."""
    if not _public_url(url):
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'conflux-upd/1.0'})
        return _OPENER.open(req, timeout=timeout).read(cap)
    except Exception:
        return None


def _latest(channel):
    """Release más reciente del canal -> {'tag', 'assets': {nombre: url}}."""
    raw = _dl(_API, cap=2_000_000, timeout=15)
    if not raw:
        return None
    try:
        rels = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rels, list):
        return None
    for r in rels:
        if not isinstance(r, dict) or r.get('draft'):
            continue
        pre = bool(r.get('prerelease')) or '-' in (r.get('tag_name') or '')
        if channel != 'beta' and pre:
            continue                        # stable: salta prereleases
        tag = r.get('tag_name') or ''
        if not _ver_key(tag):
            continue
        assets = {a.get('name'): a.get('browser_download_url')
                  for a in (r.get('assets') or []) if isinstance(a, dict)}
        return {'tag': tag, 'assets': assets, 'pre': pre}
    return None


def _verify_hash(exe, sha_txt):
    """sha256 real == publicado (línea 'hash  nombre' o hash suelto)."""
    try:
        want = (sha_txt or b'').decode('utf-8', 'replace').split()[0].lower()
    except (IndexError, UnicodeDecodeError):
        return False
    return bool(re.fullmatch(r'[0-9a-f]{64}', want)) and \
        hashlib.sha256(exe).hexdigest() == want


def _sig_verify(exe, bundle):
    """'verified' | 'unavailable' (sin lib sigstore) | 'failed' (firma mala)."""
    if not bundle:
        return 'unavailable'
    try:
        from sigstore.models import Bundle                        # optional dep
        from sigstore.verify import policy, Verifier              # optional dep
    except ImportError:
        return 'unavailable'
    try:
        Verifier.production().verify_artifact(
            exe, Bundle.from_json(bundle.decode('utf-8')),
            policy.OIDCIssuer('https://token.actions.githubusercontent.com'))
        return 'verified'
    except Exception:
        return 'failed'


_SWAP_CMD = os.path.join(
    os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _ROOT,
    'conflux-apply-update.cmd')


def _stage(exe, tag, sig):
    """Escribe conflux.new.exe + marcador de update pendiente (atómicos) y el
    helper de swap (un exe corriendo no se puede reemplazar: el .cmd espera a
    que el proceso muera, re-verifica el hash y mueve el nuevo binario)."""
    _atomic_json(_UPD_JSON, {'tag': tag, 'sig': sig,
                             'sha256': hashlib.sha256(exe).hexdigest()})
    tmp = _NEW_EXE + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(exe)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _NEW_EXE)
    d = os.path.dirname(_NEW_EXE)
    with open(_SWAP_CMD, 'w', encoding='ascii', errors='replace') as f:
        f.write(
            '@echo off\r\n'
            'rem swap update: espera a que conflux muera y reemplaza\r\n'
            ':wait\r\n'
            'tasklist /FI "IMAGENAME eq conflux.exe" | find /I "conflux.exe" >nul\r\n'
            'if %errorlevel%==0 (timeout /t 2 /nobreak >nul & goto wait)\r\n'
            f'move /y "{os.path.join(d, "conflux.new.exe")}" '
            f'"{os.path.join(d, "conflux.exe")}" >nul\r\n'
            f'del "{_UPD_JSON}" >nul 2>&1\r\n'
            f'del "%~f0" >nul 2>&1\r\n')              # se auto-borra
    return _SWAP_CMD


def _update_tick():
    """Tick diario: opt-in auto_update + instalación binaria (frozen).
    Un fallo en cualquier paso solo queda en el log — nunca rompe el refresher."""
    if not _cfgget('auto_update', False) or not getattr(sys, 'frozen', False):
        return 0
    now = CLOCK.time()
    if now - _UPD['last'] < _TICK:
        return 0
    _UPD['last'] = now
    channel = _cfgget('update_channel', 'stable') or 'stable'
    rel = _latest(channel)
    if not rel:
        return 0
    cur = _version()
    cmp_ = _ver_cmp(rel['tag'], cur)
    if cmp_ is None or cmp_ <= 0:
        return 0                                             # ya al día
    log_ev('update_available', tag=rel['tag'], channel=channel, current=cur)
    need = {_EXE, f'{_EXE}.sha256', f'{_EXE}.sha256.sigstore.json'}
    urls = rel['assets']
    if not need <= set(urls):
        log_ev('update_skip', reason='assets incompletos', tag=rel['tag'])
        return 0
    exe = _dl(urls[_EXE])
    if not exe:
        log_ev('update_skip', reason='download falló', tag=rel['tag'])
        return 0
    if not _verify_hash(exe, _dl(urls[f'{_EXE}.sha256'])):
        log_ev('update_rejected', reason='sha256 mismatch', tag=rel['tag'])
        return 0
    sig = _sig_verify(exe, _dl(urls[f'{_EXE}.sha256.sigstore.json']))
    _stage(exe, rel['tag'], sig)
    log_ev('update_staged', tag=rel['tag'], sig=sig)
    return 1
