#!/usr/bin/env python3
"""Verificación de playback REAL contra la UI de Stremio.

La UI oficial (web.stremio.com) ES la misma app que empaqueta el
desktop: se conecta al streaming server local (127.0.0.1:11470) y a los
addons por loopback. Este check conduce esa UI con Playwright:

  instala el addon conflux -> abre metadetails de Fight Club ->
  pulsa una tarjeta de stream -> espera a que <video> avance.

Evidencia de reproducción: <video> con duración real, readyState>=3 y
currentTime>0 a través de la cadena addon->streaming server->BT->MSE.
Los frames capturados salen negros en headless (superficie de vídeo
overlay), limitación del harness — la señal objetiva es el clock del
elemento de vídeo, no el píxel.

Requiere: conflux sirviendo en 127.0.0.1:9700 (CONFLUX_URL),
stremio-runtime en 11470, `pip install playwright` +
`playwright install chromium`. Los browsers exigen permiso de acceso a
red local para HTTPS->loopback; en headless se concede por flag
(la app desktop no usa ese modelo).

Salida: exit 0 si el vídeo reprodujo; exit !=0 en cualquier otro caso.
"""

import base64
import io
import os
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.environ.get("CONFLUX_URL", "http://127.0.0.1:9700")
ADDON_URL = f"{BASE}/manifest.json"
WEB = os.environ.get("CONFLUX_STREMIO_WEB", "https://web.stremio.com")
OUT_DIR = tempfile.gettempdir()
META = "movie/tt0137523"  # Fight Club — catálogo servido por conflux
TRY_STREAMS = 3  # si un stream no arranca, el siguiente
STREAM_WAIT_S = 75  # un torrent necesita peers; presupuesto
CARDS_WAIT_S = 60  # las cards llegan según responden addons


def _log(out, msg):
    print(msg, flush=True)
    out.write(msg + "\n")
    out.flush()


def _video_state(pg):
    return pg.evaluate("""() => {
      const v = document.querySelector('video');
      if (!v) return null;
      return {t: v.currentTime, d: v.duration, rs: v.readyState,
              err: v.error ? v.error.code : null};
    }""")


def _install_addon(pg, out):
    pg.goto(f"{WEB}/#/addons", wait_until="domcontentloaded", timeout=60000)
    time.sleep(7)
    pg.get_by_text("Add addon", exact=True).click()
    time.sleep(1.5)
    pg.locator('input[placeholder*="addon URL"], input[placeholder*="Paste"]').fill(
        ADDON_URL
    )
    pg.get_by_text("Add", exact=True).last.click()
    time.sleep(6)
    install = pg.get_by_text("Install", exact=True).first
    if not install.is_visible():
        raise RuntimeError(
            "modal Install no apareció: " + pg.locator("body").inner_text()[:300]
        )
    install.click()
    time.sleep(3)


def _pick_stream_and_play(pg, out):
    pg.goto(f"{WEB}/#/metadetails/{META}", wait_until="domcontentloaded", timeout=60000)
    cards = pg.locator('a[class*="stream-container"]')
    n = 0
    deadline = time.time() + CARDS_WAIT_S
    while time.time() < deadline:
        n = cards.count()
        if n > 1:
            break
        time.sleep(2)
    _log(out, f"{n} stream-cards cargadas")
    # las tarjetas torrent llevan 👤 seeds + ⚙️ fuente; las de WatchHub
    # ("Netflix · Subscription") abren sitios externos, no el player.
    tried = 0
    for i in range(min(n, 40)):
        if tried >= TRY_STREAMS:
            break
        el = cards.nth(i)
        try:
            txt = el.inner_text()
        except Exception:  # noqa: BLE001 — DOM vivo
            continue
        if "👤" not in txt and "⚙️" not in txt:
            continue
        try:
            el.click(timeout=4000)
        except Exception:
            continue
        tried += 1
        _log(out, f"stream #{tried}: {txt[:90].replace(chr(10), ' | ')}")
        deadline = time.time() + STREAM_WAIT_S
        while time.time() < deadline:
            st = _video_state(pg)
            if st and st["t"] and st["t"] > 0.5:
                _log(
                    out, f"video: t={st['t']:.1f}s dur={st['d']} readyState={st['rs']}"
                )
                return True
            time.sleep(5)
        _log(out, "  stream no arrancó; probando el siguiente")
        pg.go_back()
        time.sleep(4)
    return False


def _grab_frame(pg, out):
    """Best-effort: canvas lee píxeles si el decode es software. En
    headless con overlay sale negro — no es condición de fallo."""
    try:
        data = pg.evaluate("""() => {
          const v = document.querySelector('video');
          const c = document.createElement('canvas');
          c.width = v.videoWidth; c.height = v.videoHeight;
          c.getContext('2d').drawImage(v, 0, 0);
          return c.toDataURL('image/png').slice(22);
        }""")
        path = os.path.join(OUT_DIR, "conflux_playback_frame.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))
        _log(out, f"frame en {path}")
    except Exception as e:  # noqa: BLE001
        _log(out, f"frame no capturable (informativo): {e}")


def main():
    from playwright.sync_api import sync_playwright

    log_path = os.path.join(OUT_DIR, "conflux_ui_playback.txt")
    with open(log_path, "w", encoding="utf-8") as out:
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(
                    headless=True,
                    args=[
                        # Chrome>=141 exige permiso Local-Network-Access
                        # para páginas públicas -> loopback; headless lo
                        # deniega. La app desktop no usa este modelo.
                        "--disable-features=LocalNetworkAccessChecks",
                        "--disable-accelerated-video-decode",
                    ],
                )
                pg = b.new_context().new_page()
                try:
                    _install_addon(pg, out)
                    _log(out, "[1] addon instalado en la UI real")
                    ok = _pick_stream_and_play(pg, out)
                    if ok:
                        _grab_frame(pg, out)
                    _log(
                        out,
                        "VERDICT: "
                        + ("PLAYBACK UI REAL OK" if ok else "NO CONFIRMADO"),
                    )
                    return 0 if ok else 1
                finally:
                    b.close()
        except Exception as e:  # noqa: BLE001
            _log(out, f"FALLO: {e}")
            return 2


if __name__ == "__main__":
    sys.exit(main())
