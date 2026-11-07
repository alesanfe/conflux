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
  3. staged atómico: conflux.new.exe + conflux.update.json + helper
     conflux-apply-update.ps1 — un exe en ejecución no se puede reemplazar
     en Windows: el servidor lanza el .ps1 en shutdown; éste espera a que
     el proceso muera, re-verifica el sha256 embebido (Get-FileHash) y
     mueve el binario (con retry). En POSIX frozen el swap es directo.

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
from contextlib import suppress

from conflux.core.cfg import _cfgget
from conflux.core.state import CLOCK, _ROOT, _atomic_json, log_ev
from conflux.infra.epg import _OPENER      # redirects con re-validación SSRF
from conflux.infra.net import _public_url

_REPO = 'alesanfe/conflux'
_API = f'https://api.github.com/repos/{_REPO}/releases?per_page=10'
# asset por plataforma (matrix del release workflow): exe / linux / macos
_EXE = {'nt': 'conflux.exe', 'posix': 'conflux-linux'}.get(os.name, 'conflux.exe')
if sys.platform == 'darwin':
    _EXE = 'conflux-macos'
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
    """Semver-ish: pre-release < release del mismo num (1.0.0-beta < 1.0.0).
    Los segmentos de pre se comparan numéricamente si ambos lo son
    (beta.10 > beta.9 — léxico puro lo ordenaría al revés)."""
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
    for xa, xb in zip(pa.split('.'), pb.split('.')):
        if xa == xb:
            continue
        if xa.isdigit() and xb.isdigit():
            return -1 if int(xa) < int(xb) else 1
        return -1 if xa < xb else 1       # semver: numérico < alfanumérico
        # (un identificador numérico siempre es menor que uno alfanumérico
        #  según spec; aquí basta orden estable)
    return -1 if len(pa.split('.')) < len(pb.split('.')) else \
        (1 if len(pa.split('.')) > len(pb.split('.')) else 0)


def _version():
    try:
        import importlib.metadata
        return importlib.metadata.version('conflux')
    except Exception:
        return '0+local'


def _dl(url, cap=128_000_000, timeout=30):
    """GET bytes con redirect re-validado (_OPENER: SSRF por salto).
    cap 128MB: un onefile PyInstaller (~15-40MB) nunca trunca — si creciera
    más, el hash no casa y el update se rechaza (fail-safe), no se instala
    un binario truncado."""
    if not url or not _public_url(url):
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
                  for a in (r.get('assets') or []) if isinstance(a, dict)
                  and a.get('name') and a.get('browser_download_url')}
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


def _sig_verify(sha_bytes, bundle):
    """'verified' | 'unavailable' (sin lib sigstore) | 'failed' (firma mala).
    El bundle firma el ARCHIVO .sha256 (cosign sign-blob en el workflow), no
    el exe — verificar con los bytes del checksum, nunca con el binario."""
    if not bundle or not sha_bytes:
        return 'unavailable'
    try:
        from sigstore.models import Bundle                        # optional dep
        from sigstore.verify import policy, Verifier              # optional dep
    except ImportError:
        return 'unavailable'
    try:
        Verifier.production().verify_artifact(
            sha_bytes, Bundle.from_json(bundle.decode('utf-8')),
            policy.OIDCIssuer('https://token.actions.githubusercontent.com'))
        return 'verified'
    except Exception:
        return 'failed'


_SWAP_PS1 = os.path.join(
    os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _ROOT,
    'conflux-apply-update.ps1')


def _stage(exe, tag, sig):
    """Staged atómico: conflux.new.exe + marcador + helper de swap.

    Windows no permite reemplazar un exe en ejecución → el .ps1 espera a que
    el proceso muera, RE-VERIFICA sha256 contra el embebido (la ventana
    staged→swap es una superficie de tamper: el .cmd original movía a
    ciegas) y mueve el binario, luego relanza conflux y se auto-borra.
    PowerShell lee el .ps1 como UTF-8 (BOM): rutas con no-ASCII no se
    rompen — un .cmd en codepage OEM las corrompería (deuda del diseño
    anterior). En POSIX el exe vivo ES reemplazable: swap directo aquí."""
    sha = hashlib.sha256(exe).hexdigest()
    tmp = _NEW_EXE + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(exe)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _NEW_EXE)
    if os.name != 'nt' and getattr(sys, 'frozen', False):
        # POSIX frozen: el binario en ejecución se puede reemplazar en caliente
        # (no-frozen no llega aquí: _update_tick lo gatea; tests toman la vía nt)
        try:
            os.replace(_NEW_EXE, sys.executable)
            os.chmod(sys.executable, 0o755)
            log_ev('update_applied', tag=tag)
        except OSError as e:
            log_ev('update_stage_err', err=str(e)[:80])
        return None
    _atomic_json(_UPD_JSON, {'tag': tag, 'sig': sig, 'sha256': sha})
    # staged del diseño anterior (.cmd ciego/OEM): queda huérfano si el
    # proceso nunca se cerró — fuera junto al exe si existe
    legacy_cmd = _SWAP_PS1[:-4] + '.cmd'
    if os.path.exists(legacy_cmd):
        with suppress(OSError):
            os.remove(legacy_cmd)
    exe_path = sys.executable
    proc = os.path.splitext(os.path.basename(exe_path))[0]

    def _psq(s):                              # string literal PS: ' -> ''
        return str(s).replace("'", "''")
    with open(_SWAP_PS1 + '.tmp', 'w', encoding='utf-8-sig') as fp:
        fp.write(
            "$ErrorActionPreference='SilentlyContinue'\n"
            f"$new='{_psq(_NEW_EXE)}'\n"
            f"$exe='{_psq(exe_path)}'\n"
            f"$marker='{_psq(_UPD_JSON)}'\n"
            f"while (Get-Process -Name '{_psq(proc)}' -ErrorAction SilentlyContinue)"
            " { Start-Sleep -Seconds 2 }\n"
            f"if ((Get-FileHash $new -Algorithm SHA256).Hash -ine '{sha}') {{\n"
            "  Remove-Item $new -Force\n"
            "  Remove-Item $marker -Force\n"
            "  Remove-Item $PSCommandPath -Force\n"
            "  exit 1\n"
            "}\n"
            "foreach ($i in 1..10) {\n"            # unlock del exe puede tardar un tick
            "  try { Move-Item $new $exe -Force -ErrorAction Stop; break }\n"
            "  catch { Start-Sleep -Seconds 2 }\n"
            "}\n"
            "if (Test-Path $new) { Remove-Item $marker -Force; exit 1 }\n"
            "Remove-Item $marker -Force\n"
            "Start-Process $exe -WindowStyle Hidden\n"
            "Remove-Item $PSCommandPath -Force\n")
    os.replace(_SWAP_PS1 + '.tmp', _SWAP_PS1)
    return _SWAP_PS1


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
    sha = _dl(urls[f'{_EXE}.sha256'])
    if not _verify_hash(exe, sha):
        log_ev('update_rejected', reason='sha256 mismatch', tag=rel['tag'])
        return 0
    sig = _sig_verify(sha, _dl(urls[f'{_EXE}.sha256.sigstore.json']))
    try:
        _stage(exe, rel['tag'], sig)
    except OSError as e:                      # dir del exe no escribible (Program Files)
        log_ev('update_skip', reason=f'stage: {str(e)[:80]}', tag=rel['tag'])
        return 0
    log_ev('update_staged', tag=rel['tag'], sig=sig)
    return 1
