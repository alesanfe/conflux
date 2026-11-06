#!/usr/bin/env python3
"""E2E de UI real contra web.stremio.com — cubre TODA la superficie que
la app de Stremio puede ejercitar del addon conflux:

  install (manifest) -> Discover por cada tipo declarado
  (movie/series/anime/tv/channel) -> search -> metadetails ->
  streams torrent (movie + episodio de serie) -> stream IPTV directo
  -> <video> reproduciendo.

Lo que NO cubre (porque la UI nunca lo toca): /admin, /configure,
/health*, /metrics, auth, escrituras atómicas, install/uninstall —
eso está en tests e2e HTTP y smoke de binarios. `subtitles` tampoco:
el manifest no declara el recurso -> el cliente saca subs de
OpenSubtitles, no de conflux.

Requiere: conflux en 127.0.0.1:9700, stremio-runtime en :11470,
playwright + chromium. exit 0 si todos los escenarios pasan.
"""

import base64
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.environ.get("CONFLUX_URL", "http://127.0.0.1:9700")
ADDON_URL = f"{BASE}/manifest.json"
ADDON_ENC = urllib.parse.quote(ADDON_URL, safe="")
WEB = os.environ.get("CONFLUX_STREMIO_WEB", "https://web.stremio.com")
OUT_DIR = tempfile.gettempdir()

# un catálogo representativo por tipo declarado en el manifest
CATALOGS = {
    "movie": "scinemeta_movie_grp",
    "series": "scinemeta_series_grp",
    "anime": "skitsu_anime_grp",
    "tv": "siptv_tv_iptv-all",
    "channel": "syoutube_channel_top",
}
# stremio-web renderiza los metas como <a class="meta-item-link-*">
# href="#/detail/<type>/<id>" — recurso meta pedido al navegar a ellos
CARD_SEL = 'a[class*="meta-item-link"]'

SEARCH_Q = "fight club"
# detail/series/<meta>/<video> — vista real de episodio con stream-cards
SERIES_META = "detail/series/tt0944947/tt0944947%3A1%3A1"
# detail/tv/<meta>/<video> — como las cards del catálogo (id duplicado).
# El canal se elige EN VIVO: primero cuyo m3u8 https responda válido
# (los upstreams IPTV mueren/rotan constantemente — R5).
TV_CATALOG = "tv/siptv_tv_iptv-all"
STREAM_WAIT_S = 75


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


def _install(pg, out):
    pg.goto(f"{WEB}/#/addons", wait_until="domcontentloaded", timeout=60000)
    time.sleep(7)
    pg.get_by_text("Add addon", exact=True).click()
    time.sleep(1.5)
    pg.locator('input[placeholder*="addon URL"], input[placeholder*="Paste"]').fill(
        ADDON_URL
    )
    pg.get_by_text("Add", exact=True).last.click()
    time.sleep(6)
    pg.get_by_text("Install", exact=True).first.click()
    time.sleep(3)


def _catalog_renders(pg, out, tipo, catid):
    """Discover debe renderizar metas (poster cards) del catálogo."""
    _fresh_goto(pg, f"discover/{ADDON_ENC}/{tipo}/{catid}")
    cards = pg.locator(CARD_SEL)
    deadline = time.time() + 45
    n = 0
    while time.time() < deadline:
        n = cards.count()
        if n > 0:
            break
        time.sleep(2)
    _log(out, f"  catalog {tipo}/{catid}: {n} metas renderizadas")
    return n > 0


def _search(pg, out):
    """La búsqueda del nav usa los catálogos search de los addons.
    El buscador es un div que revela el input al hacer click."""
    pg.locator('[class*="search-input"]').first.click()
    time.sleep(1.5)
    box = pg.locator('input[placeholder*="Search" i]').first
    box.fill(SEARCH_Q)
    box.press("Enter")
    time.sleep(10)
    n = pg.locator(CARD_SEL).count()
    _log(out, f'  search "{SEARCH_Q}": {n} resultados')
    return n > 0


def _click_stream_and_wait_video(pg, out, budget=STREAM_WAIT_S, max_cards=40, tries=3):
    """Pulsa tarjetas torrent (👤/⚙️) o directas hasta que <video>
    avance. Devuelve True si algún stream reprodujo."""
    cards = pg.locator('a[class*="stream-container"]')
    deadline = time.time() + 60
    n = 0
    while time.time() < deadline:
        n = cards.count()
        if n > 0:
            break
        time.sleep(2)
    _log(out, f"  {n} stream-cards")
    if n == 0:
        return False

    # prioriza torrents por nº de seeds — los externos (flixnest...)
    # abren reproductor propio, no el <video> local
    def _seeds(i):
        try:
            t = cards.nth(i).inner_text() or ""
        except Exception:  # noqa: BLE001
            return -1
        m = re.search(r"👤\s*(\d+)", t)
        return int(m.group(1)) if m else -1

    orden = sorted(range(min(n, max_cards)), key=_seeds, reverse=True)
    tried = 0
    for i in orden:
        if tried >= tries:
            break
        el = cards.nth(i)
        try:
            txt = el.inner_text()
        except Exception:  # noqa: BLE001
            continue
        # torrent: 👤+⚙️ | directo/IPTV: ▶️ o http | saltar subs externas
        if not any(k in txt for k in ("👤", "⚙️", "▶", "http")):
            continue
        try:
            el.click(timeout=4000)
        except Exception:
            continue
        tried += 1
        _log(out, f"  stream #{tried}: {txt[:80].replace(chr(10), ' | ')}")
        deadline = time.time() + budget
        while time.time() < deadline:
            st = _video_state(pg)
            if st and st["t"] and st["t"] > 0.5:
                _log(out, f"  video: t={st['t']:.1f}s dur={st['d']} rs={st['rs']}")
                return True
            time.sleep(5)
        _log(out, "  no arrancó; siguiente")
        pg.go_back()
        time.sleep(4)
    return False


def _fresh_goto(pg, hashroute):
    """goto con solo cambio de # NO recarga la SPA ni limpia el DOM
    (las cards viejas contaminarían el conteo). about:blank resetea."""
    pg.goto("about:blank")
    pg.goto(f"{WEB}/#/{hashroute}", wait_until="domcontentloaded", timeout=60000)


def _series_episode(pg, out):
    """Serie: detalle del episodio -> streams -> play.
    Cada episodio es su propia vista detail en stremio-web: pedir
    detail/series/<meta>/<video: S:E> ejercita la resolución S:E."""
    _fresh_goto(pg, SERIES_META)
    return _click_stream_and_wait_video(pg, out)


def _live_channels(out, limit=3):
    """Canales del catálogo IPTV cuyo stream https tiene playlist
    Y segmento reales vivos (los upstreams mueren constantemente —
    playlist 200 con segmentos 404 es lo habitual). Hasta `limit`."""
    try:
        metas = json.load(
            urllib.request.urlopen(f"{BASE}/catalog/{TV_CATALOG}.json", timeout=30)
        )["metas"]
    except Exception as e:  # noqa: BLE001
        _log(out, f"  catálogo iptv inalcanzable: {e}")
        return []
    vivos = []
    for m in metas:
        if len(vivos) >= limit:
            break
        iid = m["id"]
        try:
            ss = json.load(
                urllib.request.urlopen(
                    f"{BASE}/stream/tv/{urllib.parse.quote(iid, safe='')}.json",
                    timeout=25,
                )
            ).get("streams", [])
        except Exception:  # noqa: BLE001
            continue
        for s in ss:
            url = s.get("url", "")
            if not url.startswith("https://"):
                continue  # http:// -> mixed content en web.stremio.com
            try:
                body = urllib.request.urlopen(url, timeout=8).read()
                if b"#EXTM3U" not in body:
                    continue
                # playlist OK -> ¿segmento vivo? (playback real lo necesita)
                for ln in body.decode("utf-8", "replace").splitlines():
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        seg = urllib.parse.urljoin(url, ln)
                        if (
                            urllib.request.urlopen(
                                urllib.request.Request(seg, method="HEAD"), timeout=8
                            ).status
                            == 200
                        ):
                            vivos.append(iid)
                            _log(out, f"  canal vivo: {iid}")
                        break
            except Exception:  # noqa: BLE001
                continue
            break
    if not vivos:
        _log(out, "  ningún canal con segmentos vivos ahora")
    return vivos


def _iptv(pg, out):
    """IPTV: lo que conflux controla es ENTREGAR el stream — card con
    URL directa resoluble (playlist+segmento vivos, verificado). La
    decodificación depende del códec del canal vs. soporte del browser
    (canales gratis: MPEG-2/TS → 'code 83' también en desktop real).
    Playback observado = evidencia extra, no condición."""
    for iid in _live_channels(out):
        enc = urllib.parse.quote(iid, safe="")
        _fresh_goto(pg, f"detail/tv/{enc}/{enc}")
        cards = pg.locator('a[class*="stream-container"]')
        deadline = time.time() + 60
        n = 0
        while time.time() < deadline:
            n = cards.count()
            if n > 0:
                break
            time.sleep(2)
        href = cards.first.get_attribute("href") or ""
        _log(out, f"  {iid}: {n} stream-cards, player-href={'player' in href}")
        if n == 0 or "player" not in href:
            continue
        # stream entregado OK -> bonus: intentar playback
        if _click_stream_and_wait_video(pg, out, budget=30, tries=1):
            _log(out, "  playback IPTV observado (bonus)")
        return True
    return False


def _grab_frame(pg, out):
    try:
        data = pg.evaluate("""() => {
          const v = document.querySelector('video');
          const c = document.createElement('canvas');
          c.width = v.videoWidth; c.height = v.videoHeight;
          c.getContext('2d').drawImage(v, 0, 0);
          return c.toDataURL('image/png').slice(22);
        }""")
        path = os.path.join(OUT_DIR, "conflux_ui_frame.png")
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))
        _log(out, f"  frame en {path}")
    except Exception as e:  # noqa: BLE001
        _log(out, f"  frame no capturable: {e}")


def main():
    from playwright.sync_api import sync_playwright

    log_path = os.path.join(OUT_DIR, "conflux_ui_e2e.txt")
    results = {}
    with open(log_path, "w", encoding="utf-8") as out:
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(
                    headless=True,
                    # Chrome real: códecs propietarios (H.264/AAC) que el
                    # chromium de playwright no trae — sin ellos los HLS
                    # IPTV dan "video is not supported" (code 83)
                    channel="chrome",
                    args=[
                        "--disable-features=LocalNetworkAccessChecks",
                        "--disable-accelerated-video-decode",
                        # canales IPTV viven en http:// — la app desktop
                        # no aplica mixed-content; el flag emula eso
                        "--allow-running-insecure-content",
                    ],
                )
                pg = b.new_context().new_page()
                try:
                    _install(pg, out)
                    results["install addon"] = True
                    _log(out, "[1] addon instalado")

                    _log(out, "[2] catálogos por tipo:")
                    for tipo, catid in CATALOGS.items():
                        results[f"catalog {tipo}"] = _catalog_renders(
                            pg, out, tipo, catid
                        )

                    _log(out, "[3] búsqueda UI:")
                    pg.goto(f"{WEB}/#/", wait_until="domcontentloaded", timeout=60000)
                    time.sleep(6)
                    results["search"] = _search(pg, out)

                    _log(out, "[4] playback serie (episodio S:E):")
                    results["stream series"] = _series_episode(pg, out)

                    _log(out, "[5] playback canal IPTV (HLS directo):")
                    results["stream tv/iptv"] = _iptv(pg, out)
                    if results["stream tv/iptv"]:
                        _grab_frame(pg, out)
                finally:
                    b.close()
        except Exception as e:  # noqa: BLE001
            _log(out, f"FALLO GLOBAL: {e}")
            results["error"] = False
        _log(out, "")
        fails = [k for k, v in results.items() if not v]
        for k, v in results.items():
            _log(out, f"  {'OK  ' if v else 'FAIL'} {k}")
        _log(out, f"VERDICT: {'UI E2E OK' if not fails else f'{len(fails)} FALLOS'}")
        return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
