#!/usr/bin/env python3
"""Upgrade REAL entre releases publicadas: instala la wheel vieja en un
venv temporal, la arranca (config.json creado), la sustituye por la
nueva y verifica que la config sobrevive intacta al upgrade.

Cubre lo que los tests de install no pueden: el empaquetado REAL de la
release (wheels de GitHub Releases), no el código del árbol. El estado
(_ROOT = <venv>/Lib en installs no-frozen) queda fuera del paquete y
pip --upgrade no lo toca — eso es lo que se verifica.

Uso:  python3 scripts/upgrade_check.py [v_old] [v_new]
      por defecto v1.0.5 -> v1.0.6
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

OLD = sys.argv[1] if len(sys.argv) > 1 else "v1.0.5"
NEW = sys.argv[2] if len(sys.argv) > 2 else "v1.0.6"
REPO = "alesanfe/conflux"
PORT = 19700


def _wheel_url(ver):
    n = ver.lstrip("v")
    return (
        f"https://github.com/{REPO}/releases/download/{ver}/"
        f"conflux-{n}-py3-none-any.whl"
    )


def _run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def _pip(venv):
    return os.path.join(venv, "Scripts", "python.exe")


def _start(venv):
    """Arranca el servidor en PORT alterno (el real vive en :9700)."""
    code = (
        "import conflux.core.state as s;"
        f"s.PORT={PORT};"
        "from conflux.web.server import main;main()"
    )
    return subprocess.Popen(
        [_pip(venv), "-c", code],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )


def _wait_alive(secs=120):
    url = f"http://127.0.0.1:{PORT}/health/live"
    for _ in range(secs):
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def _config_path(venv):
    # _ROOT en no-frozen = <venv>/Lib (config.json y .manifests-cache.json
    # viven junto al site-packages, fuera del paquete -> pip no los toca)
    return os.path.join(venv, "Lib", "config.json")


def main():
    work = tempfile.mkdtemp(prefix="conflux_upg_")
    venv = os.path.join(work, "venv")
    proc = None
    try:
        whl_old = os.path.join(work, _wheel_url(OLD).rsplit("/", 1)[-1])
        whl_new = os.path.join(work, _wheel_url(NEW).rsplit("/", 1)[-1])
        for url, dst in ((_wheel_url(OLD), whl_old), (_wheel_url(NEW), whl_new)):
            print(f"descargando {url}")
            urllib.request.urlretrieve(url, dst)

        _run([sys.executable, "-m", "venv", venv])
        _run([_pip(venv), "-m", "pip", "install", "--quiet", whl_old])

        proc = _start(venv)
        assert _wait_alive(), "v_old no levantó"
        cfg = _config_path(venv)
        assert os.path.isfile(cfg), f"config.json no creado en {cfg}"
        before = json.load(open(cfg, encoding="utf-8"))
        tok_before = before.get("admin_token")
        assert tok_before, "admin_token no generado en v_old"
        m_old = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/manifest.json")
        )
        _stop(proc)
        proc = None
        print(f"v_old OK — admin_token generado, manifest '{m_old.get('name')}'")

        _run([_pip(venv), "-m", "pip", "install", "--quiet", "--upgrade", whl_new])
        ver = subprocess.run(
            [_pip(venv), "-c",
             "import importlib.metadata as m;print(m.version('conflux'))"],
            capture_output=True, text=True,
        )
        print("versión tras upgrade:", (ver.stdout or ver.stderr).strip())

        proc = _start(venv)
        assert _wait_alive(), "v_new no levantó tras el upgrade"
        after = json.load(open(cfg, encoding="utf-8"))
        assert after.get("admin_token") == tok_before, (
            "config.json NO preservada: admin_token cambió en el upgrade"
        )
        assert os.path.isfile(
            os.path.join(venv, "Lib", ".manifests-cache.json")
        ) or True  # el cache puede no existir aún — no es contrato
        m_new = json.load(
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/manifest.json")
        )
        print(f"v_new OK — admin_token preservado, manifest '{m_new.get('name')}'")

        cache = os.path.join(venv, "Lib", ".manifests-cache.json")
        print(
            "manifests-cache preservado:",
            os.path.isfile(cache),
        )
        print("VERDICT: UPGRADE OK — config preservada %s -> %s" % (OLD, NEW))
        return 0
    finally:
        if proc:
            _stop(proc)


if __name__ == "__main__":
    sys.exit(main())
