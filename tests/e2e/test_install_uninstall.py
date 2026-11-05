"""E2E install -> uninstall del entorno real (scripts/installer.py).

Ejercita las MISMAS funciones del wizard (kill_proxy / install_proxy /
remove_autostart / remove_runtime / install_addon) contra la máquina real:
ciclo uninstall -> limpio -> install -> montado -> uninstall -> limpio ->
install final (la máquina queda funcionando).

Mutante del sistema real (Startup, PID :9700, API remota de Stremio):
OPT-IN — no corre en suite ni CI.

    CONFLUX_INSTALL_E2E=1 python -m unittest tests.e2e.test_install_uninstall

No cubre: creación de .venv (bootstrap OS-dependiente del paso 0 — el
reinstall usa la rama idempotente .venv-existente, la realista en esta
máquina), Prowlarr/winget (requiere UAC) ni docker-usenet (opcional).
"""
import json
import os
import sys
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
import installer  # noqa: E402
import install_addon  # noqa: E402

PROXY = 'http://127.0.0.1:9700'
LNK = os.path.join(os.environ.get('APPDATA', ''),
                   'Microsoft\\Windows\\Start Menu\\Programs\\Startup',
                   'Conflux.lnk')
GENERATED = ['start-conflux.bat', '.manifests-cache.json', 'epg-cache.json',
             'conflux.log']
# .venv se conserva en el ciclo (rebuild = pip install completo, OS-dependiente);
# .env/config.json/sources.json se conservan (keep_cfg: claves del usuario).
SKIP = ('.venv',)


def _up(path='/health', timeout=3):
    try:
        urllib.request.urlopen(f'{PROXY}{path}', timeout=timeout)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_down(secs=20):
    """El proceso muere por taskkill async — espera a que :9700 cierre."""
    for _ in range(secs):
        if not _up():
            return True
        time.sleep(1)
    return False


@unittest.skipUnless(os.environ.get('CONFLUX_INSTALL_E2E'),
                     'install e2e muta la máquina — CONFLUX_INSTALL_E2E=1')
@unittest.skipUnless(sys.platform == 'win32', 'autostart/proxy: rutas win32')
class TestInstallUninstall(unittest.TestCase):
    maxDiff = None

    def test_ciclo_completo(self):
        # ── FASE 1: uninstall (la máquina viene instalada) ──
        installer.kill_proxy()
        self.assertTrue(_wait_down(), 'el proxy sigue escuchando tras taskkill')
        installer.remove_autostart()
        self.assertFalse(os.path.exists(LNK), 'Conflux.lnk sigue en Startup')
        installer.remove_addon()          # remoto puede ser 'manual' — no falla
        installer.remove_runtime(keep_cfg=True, skip=SKIP)
        for p in GENERATED:
            self.assertFalse(os.path.exists(os.path.join(installer.ROOT, p)),
                             f'{p} no se eliminó')
        self.assertFalse(_up(), ':9700 sigue respondiendo tras uninstall')

        # ── FASE 2: install -> todo se monta ──
        self.assertTrue(installer.install_proxy(),
                        'install_proxy: el proxy no levantó en 60s')
        self.assertTrue(os.path.exists(LNK), 'autostart no se recreó')
        self.assertTrue(os.path.exists(
            os.path.join(installer.ROOT, 'start-conflux.bat')))
        man = json.load(urllib.request.urlopen(f'{PROXY}/manifest.json',
                                               timeout=10))
        self.assertTrue(man.get('catalogs'), 'manifest sin catálogos')
        self.assertTrue(man.get('idPrefixes'), 'manifest sin idPrefixes (R3)')
        for ep in ('/admin', '/configure'):
            self.assertTrue(_up(ep), f'{ep} no responde tras install')
        st, msg = install_addon.install(f'{PROXY}/manifest.json')
        self.assertNotEqual(st, 'error',
                            f'install_addon falló: {msg}')

        # ── FASE 3: uninstall -> limpio otra vez ──
        installer.kill_proxy()
        self.assertTrue(_wait_down(), 'el proxy no murió en el 2º uninstall')
        installer.remove_autostart()
        installer.remove_runtime(keep_cfg=True, skip=SKIP)
        self.assertFalse(os.path.exists(LNK))
        self.assertFalse(_up(), ':9700 respondiendo tras 2º uninstall')

        # ── FASE 4: install final — la máquina queda montada ──
        self.assertTrue(installer.install_proxy(),
                        'el reinstall final no levantó el proxy')
        self.assertTrue(os.path.exists(LNK))
        man2 = json.load(urllib.request.urlopen(f'{PROXY}/manifest.json',
                                                timeout=10))
        self.assertTrue(man2.get('catalogs'))


if __name__ == '__main__':
    unittest.main()
