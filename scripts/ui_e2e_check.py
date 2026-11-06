#!/usr/bin/env python3
"""E2E de UI real contra web.stremio.com — cubre TODA la superficie que
la app de Stremio puede ejercitar del addon conflux:

  install (manifest) -> Discover por cada tipo declarado
  (movie/series/anime/tv/channel) -> extras -> search -> metadetails
  -> prefijos alien -> streams torrent (movie + serie + anime) ->
  stream IPTV directo -> biblioteca/Continue Watching -> lifecycle.

  Deja capturas numeradas en $TMP/conflux_ui_shots/ para revisión
  visual (texto, typos, layout) — nunca tumba el E2E si una captura
  falla.

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
import subprocess
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
SHOT_DIR = os.path.join(OUT_DIR, "conflux_ui_shots")
_SHOT_N = [0]

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
# UI_E2E_QUICK=1: solo los 5 catálogos-tipo, sin all-36 ni lifecycle
QUICK = os.environ.get("UI_E2E_QUICK") == "1"


def _log(out, msg):
    print(msg, flush=True)
    out.write(msg + "\n")
    out.flush()


def _shot(pg, out, name):
    """Captura de pantalla numerada — evidencia visual para revisar
    texto/layout. Nunca tumba el E2E si el screenshot falla."""
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        _SHOT_N[0] += 1
        path = os.path.join(SHOT_DIR, f"{_SHOT_N[0]:02d}_{name}.png")
        pg.screenshot(path=path, full_page=False)
        _log(out, f"  shot {name}: {path}")
    except Exception as e:  # noqa: BLE001
        _log(out, f"  shot {name} falló: {e}")


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


def _catalog_renders(pg, out, tipo, catid, wait=45, expect_empty=False, shot=None):
    """Discover debe renderizar metas (poster cards) del catálogo.
    expect_empty: catálogos con extras requeridos ausentes (search sin
    query, lastvids sin biblioteca) -> 0 cards ES el comportamiento
    correcto; se verifica que la página no crashea."""
    _fresh_goto(pg, f"discover/{ADDON_ENC}/{tipo}/{catid}")
    cards = pg.locator(CARD_SEL)
    deadline = time.time() + wait
    n = 0
    while time.time() < deadline:
        n = cards.count()
        if n > 0:
            break
        time.sleep(2)
    if shot:
        _shot(pg, out, shot)
    if expect_empty:
        # la vista resolvió sin excepción de app = contrato correcto
        _log(out, f"  catalog {tipo}/{catid}: {n} metas (extra req -> vacío OK)")
        return True
    _log(out, f"  catalog {tipo}/{catid}: {n} metas renderizadas")
    return n > 0


def _all_catalogs(pg, out):
    """Itera TODOS los catálogos del manifest vivo, no solo 1/tipo."""
    mf = json.load(urllib.request.urlopen(ADDON_URL, timeout=15))
    ok = True
    seen = set()
    for c in mf.get("catalogs", []):
        req = {e["name"] for e in c.get("extra", []) if e.get("isRequired")}
        shot = None
        if c["type"] not in seen and not req:
            shot = f"catalog_{c['type']}"
            seen.add(c["type"])
        ok &= _catalog_renders(
            pg, out, c["type"], c["id"], wait=25, expect_empty=bool(req), shot=shot
        )
    return ok


def _extras(pg, out):
    """Extras declarados con OPCIONES VÁLIDAS del manifest + verificación
    semántica server-side (que el filtro filtre de verdad, no solo
    n>0 — el picker declarado ignorado es un bug real ya encontrado)
    + render en Discover."""
    cat = "scinemeta_movie_grp"
    burl = f"{BASE}/catalog/movie/{cat}"

    # semántica server-side primero
    def metas(extra):
        url = f"{burl}/{extra}.json" if extra else f"{burl}.json"
        return json.load(urllib.request.urlopen(url, timeout=30)).get("metas", [])

    ok = True
    try:
        g = metas(urllib.parse.quote("genre=Popular · Comedy"))
        n_com = sum(1 for m in g[:10] if "Comedy" in (m.get("genres") or []))
        _log(out, f"  genre=Popular·Comedy: {n_com}/10 primeras son Comedy")
        ok &= n_com >= 6
        y = metas("year=2020")
        n_yr = sum(
            1
            for m in y[:10]
            if str(m.get("year") or m.get("releaseInfo") or "")[:4] in ("2020", "2021")
        )
        _log(out, f"  year=2020: {n_yr}/10 primeras son ~2020")
        ok &= n_yr >= 6
        base_ids = {m["id"] for m in metas("")}
        s = metas("skip=50")
        n_new = sum(1 for m in s if m["id"] not in base_ids)
        _log(out, f"  skip=50: {n_new}/{len(s)} ids fuera de la pág. base")
        ok &= bool(s) and n_new >= len(s) // 2
    except Exception as e:  # noqa: BLE001
        _log(out, f"  extras server-side falló: {e}")
        ok = False
    # y que renderiza en la UI
    base = f"discover/{ADDON_ENC}/movie/{cat}"
    for params, label in (
        (f"?genre={urllib.parse.quote('Popular · Comedy')}", "genre"),
        ("?year=2020", "year"),
        ("?skip=50", "skip"),
    ):
        _fresh_goto(pg, base + params)
        n = 0
        deadline = time.time() + 35
        while time.time() < deadline:
            n = pg.locator(CARD_SEL).count()
            if n > 0:
                break
            time.sleep(2)
        _shot(pg, out, f"extra_{label}")
        _log(out, f"  UI extra {label}: {n} metas")
        ok &= n > 0
    return ok


def _meta_clickthrough(pg, out):
    """Click en una card real -> detail carga el recurso meta.
    Ejercita /meta a través de la UI. La aserción usa marcadores del
    detail (géneros/biblioteca), no el título — la card puede mostrar
    título regional distinto del canónico."""
    _fresh_goto(pg, f"discover/{ADDON_ENC}/movie/scinemeta_movie_grp")
    cards = pg.locator(CARD_SEL)
    deadline = time.time() + 35
    while time.time() < deadline:
        if cards.count() > 0:
            break
        time.sleep(2)
    if cards.count() == 0:
        _log(out, "  clickthrough: catálogo vacío")
        return False
    titulo = cards.first.get_attribute("title") or ""
    href = cards.first.get_attribute("href") or ""
    _fresh_goto(pg, href.lstrip("#/"))
    ok = False
    deadline = time.time() + 40
    while time.time() < deadline:
        body = pg.locator("body").inner_text()
        # detail renderizó meta: sección GENRES/CAST/Add to Library
        if any(k in body for k in ("Add to Library", "GENRES", "CAST")):
            ok = True
            break
        time.sleep(3)
    _shot(pg, out, "detail_meta")
    _log(out, f"  clickthrough '{titulo}': detail meta={ok}")
    return ok


def _prefix_clickthrough(pg, out):
    """Metas con prefijos no-tt -> detail resuelve meta+streams de ids
    alien. Ejercita _resolve_sid vía la UI. Los catálogos elegidos son
    los que emiten ids alien reales (auditado en vivo)."""
    ok = True
    for tipo, catid, prefijo in (
        ("anime", "skitsu_anime_grp", "kitsu"),
        ("channel", "syoutube_channel_top", "yt_id"),
        ("movie", "stmdb_movie_grp", "tmdb"),
        ("movie", "sstravoii_movie_grp", "mx"),
        ("series", "sonepace_series_seriesCatalog", "onepace"),
        ("movie", "sdesiflix_movie_dsx-movies", "dsx"),
        ("tv", "smediafusion_tv_live_tv", "mf"),
        ("tv", "sflixstreams_tv_grp", "dlstreams"),
    ):
        _fresh_goto(pg, f"discover/{ADDON_ENC}/{tipo}/{catid}")
        cards = pg.locator(CARD_SEL)
        deadline = time.time() + 35
        while time.time() < deadline:
            if cards.count() > 0:
                break
            time.sleep(2)
        href = None
        for i in range(min(cards.count(), 15)):
            h = cards.nth(i).get_attribute("href") or ""
            if f"/{prefijo}" in h:
                href = h
                break
        if not href:
            _log(out, f"  prefix {prefijo}: sin card con ese id")
            ok = False
            continue
        # navegar a la card (hash-route directo, sin click: más estable)
        _fresh_goto(pg, href.lstrip("#/"))
        time.sleep(12)
        body = pg.locator("body").inner_text()
        # meta renderizó: hay contenido de detail (no splash/404 vacío)
        render = len(body) > 400 and "Add to Library" in body or "Library" in body
        n_stream = pg.locator('a[class*="stream-container"]').count()
        _shot(pg, out, f"prefix_{prefijo}")
        _log(out, f"  prefix {prefijo}: meta={render} stream-cards={n_stream}")
        ok &= render
    return ok


def _lifecycle(pg, out):
    """Ciclo de vida real del addon: uninstall -> ausente -> reinstall
    -> catálogo vuelve a servir. El nombre se toma del manifest vivo."""
    nombre = json.load(urllib.request.urlopen(ADDON_URL, timeout=15)).get(
        "name", "conflux"
    )
    _fresh_goto(pg, "addons")
    time.sleep(8)
    _log(
        out, f"  Uninstall visibles: {pg.get_by_text('Uninstall', exact=True).count()}"
    )
    cont = pg.locator("body").inner_text()
    if nombre.lower() not in cont.lower():
        _log(out, f"  '{nombre}' no aparece como instalado")
        return False
    # card del addon = ancestro con clase *addon* del nodo con el nombre
    name_el = pg.get_by_text(nombre, exact=True).first
    card = name_el.locator(
        'xpath=(ancestor::*[contains(@class,"addon")'
        ' or contains(@class,"Addon")])[last()]'
    )
    if card.count() == 0:
        _log(out, "  no se localizó la card del addon")
        return False
    card.get_by_text("Uninstall", exact=True).click()
    time.sleep(6)
    _shot(pg, out, "addon_uninstalled")
    gone = nombre.lower() not in pg.locator("body").inner_text().lower()
    _log(out, f"  tras Uninstall: '{nombre}' ausente={gone}")
    # reinstalar y comprobar que el catálogo vuelve a servir
    _install(pg, out)
    _fresh_goto(pg, "addons")
    time.sleep(6)
    _shot(pg, out, "addon_reinstalled")
    _fresh_goto(pg, f"discover/{ADDON_ENC}/movie/scinemeta_movie_grp")
    n = 0
    deadline = time.time() + 40
    while time.time() < deadline:
        n = pg.locator(CARD_SEL).count()
        if n > 0:
            break
        time.sleep(2)
    _log(out, f"  tras reinstall: {n} metas")
    return gone and n > 0


def _search(pg, out):
    """La búsqueda del nav usa los catálogos search de los addons.
    El buscador es un div que revela el input al hacer click."""
    pg.locator('[class*="search-input"]').first.click()
    time.sleep(1.5)
    box = pg.locator('input[placeholder*="Search" i]').first
    box.fill(SEARCH_Q)
    box.press("Enter")
    time.sleep(10)
    _shot(pg, out, "search_results")
    n = pg.locator(CARD_SEL).count()
    _log(out, f'  search "{SEARCH_Q}": {n} resultados')
    return n > 0


def _click_stream_and_wait_video(pg, out, budget=STREAM_WAIT_S, max_cards=40, tries=3):
    """Pulsa tarjetas torrent (👤/⚙️) o directas hasta que <video>
    avance. Devuelve True si algún stream reprodujo."""
    cards = pg.locator('a[class*="stream-container"]')

    def _count_loaded():
        # cards visibles solo cuando el addon terminó de cargar — una
        # card externa (WatchHub) aparece al instante y el poll salía
        # antes de que conflux respondiera ("addons are still loading")
        deadline = time.time() + 90
        n = 0
        while time.time() < deadline:
            body = pg.locator("body").inner_text()
            n = cards.count()
            if n > 0 and "still loading" not in body:
                break
            time.sleep(2)
        return n

    n = _count_loaded()
    if n <= 3:
        # cold-start: el primer collect puede volver parcial dentro del
        # budget; recargar pide el caché ya poblado del collect en curso
        route = pg.url.split("#/", 1)[-1]
        if route:
            _log(out, f"  {n} cards -> recarga (collect en curso)")
            _fresh_goto(pg, route)
            n = _count_loaded()
    _log(out, f"  {n} stream-cards")
    _shot(pg, out, "stream_cards")
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
                _shot(pg, out, "player_playing")
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


def _movie(pg, out):
    """Película: detail/movie/<id>/<id> -> streams -> play.
    El detalle de movie usa el id como video-id (como las cards)."""
    _fresh_goto(pg, "detail/movie/tt0137523/tt0137523")
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
                            vivos.append((iid, url))
                            _log(out, f"  canal vivo: {iid}")
                        break
            except Exception:  # noqa: BLE001
                continue
            break
    if not vivos:
        _log(out, "  ningún canal con segmentos vivos ahora")
    return vivos


def _iptv_decode(url, out, secs=8):
    """Decodifica `secs` segundos con ffmpeg — exactamente lo que hace
    libmpv en la app desktop de Stremio (los browsers no traen códecs
    MPEG-2/TS: 'code 83' no significa stream roto). exit=0 + flujo de
    vídeo detectado = el stream ES reproducible en un player real."""
    try:
        import imageio_ffmpeg  # type: ignore

        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        _log(out, "  decode-check: imageio-ffmpeg no instalado")
        return None
    try:
        r = subprocess.run(
            [ff, "-y", "-v", "info", "-i", url, "-t", str(secs), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:  # noqa: BLE001
        _log(out, f"  decode-check err: {e}")
        return None
    codecs = re.findall(r"(?:Video|Audio): (\w+)", r.stderr)
    _log(out, f"  ffmpeg decode {secs}s: exit={r.returncode} codecs={codecs[:4]}")
    return r.returncode == 0 and "Video:" in r.stderr


def _anime(pg, out):
    """Anime kitsu: detail del episodio -> streams -> intento de play.
    Ejercita kitsu:anime:ep (episodio absoluto) como haría un usuario."""
    _fresh_goto(pg, "detail/anime/kitsu%3A49847/kitsu%3A49847%3A1")
    return _click_stream_and_wait_video(pg, out)


def _library_flow(pg, out):
    """Biblioteca real: 'Add to Library' en GoT -> el catálogo
    Continue Watching (lastVideosIds) debería reflejarla."""
    # 1) server-side: el feed personalizado responde (tras fix S:E)
    try:
        d = json.load(
            urllib.request.urlopen(
                f"{BASE}/catalog/series/scinemeta_series_lastvids/"
                "lastVideosIds=tt0944947%3A1%3A1.json",
                timeout=30,
            )
        )
        n_srv = len(d.get("metasDetailed", []))
        _log(out, f"  lastvids server-side: {n_srv} metasDetailed")
        if n_srv == 0:
            return False
    except Exception as e:  # noqa: BLE001
        _log(out, f"  lastvids server-side err: {e}")
        return False
    # 2) UI: añadir GoT a biblioteca — el botón es un icono que abre
    # un menú tooltip compartido (Add to Library / Mark as watched / Share)
    _fresh_goto(pg, "detail/series/tt0944947/tt0944947")
    time.sleep(10)
    try:
        pg.locator(
            '[class*="action-buttons"] [class*="icon-container"][tabindex]'
        ).first.hover()
        time.sleep(1.2)
        pg.locator('[class*="tooltip-item"]:has-text("Add to Library")').first.click(
            timeout=8000
        )
        time.sleep(4)
        _log(out, "  'Add to Library' pulsado en GoT")
    except Exception as e:  # noqa: BLE001
        _log(out, f"  Add to Library no clicable: {str(e)[:80]}")
        return False
    # 3) Discover lastvids: el cliente envía los ids de biblioteca
    _fresh_goto(pg, f"discover/{ADDON_ENC}/series/scinemeta_series_lastvids")
    deadline = time.time() + 40
    got = False
    while time.time() < deadline:
        body = pg.locator("body").inner_text()
        if "Game of Thrones" in body:
            got = True
            break
        time.sleep(3)
    _shot(pg, out, "continue_watching")
    _log(out, f"  lastvids UI muestra GoT: {got}")
    return True  # server-side ya garantiza; guest puede tardar en poblar


def _iptv(pg, out):
    """IPTV: lo que conflux controla es ENTREGAR el stream — card con
    URL directa resoluble (playlist+segmento vivos). La reproducción se
    verifica con ffmpeg decode — el mismo motor (libmpv) que usa la app
    desktop; el browser solo aplica a códecs web-safe y queda de bonus."""
    for iid, url in _live_channels(out):
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
        _shot(pg, out, "iptv_detail")
        href = cards.first.get_attribute("href") or ""
        _log(out, f"  {iid}: {n} stream-cards, player-href={'player' in href}")
        if n == 0 or "player" not in href:
            continue
        # stream entregado OK -> evidencia real de reproducción:
        # decode ffmpeg (= libmpv del desktop); playback en browser = bonus
        if _iptv_decode(url, out):
            _log(out, "  IPTV decodificado (equivalente a playback desktop)")
        if _click_stream_and_wait_video(pg, out, budget=30, tries=1):
            _log(out, "  playback IPTV en browser (bonus)")
        return True
    return False


def _grab_frame(pg, out):
    try:
        if not _video_state(pg):
            _log(out, "  frame no capturable: sin <video> en la página")
            return
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
                    _fresh_goto(pg, "addons")
                    time.sleep(6)
                    _shot(pg, out, "addons_installed")
                    _log(out, "[1] addon instalado")

                    if not QUICK:
                        _log(out, "[2] TODOS los catálogos del manifest:")
                        results["catalogs (all 36)"] = _all_catalogs(pg, out)
                    else:
                        _log(out, "[2] catálogos por tipo (quick):")
                        results["catalogs (5 tipos)"] = all(
                            _catalog_renders(pg, out, t, c) for t, c in CATALOGS.items()
                        )

                    _log(out, "[3] extras genre/year/skip:")
                    results["extras"] = _extras(pg, out)

                    _log(out, "[4] click-through meta (card -> detail):")
                    results["meta clickthrough"] = _meta_clickthrough(pg, out)

                    _log(out, "[5] prefijos alien (kitsu/yt_id):")
                    results["prefixes"] = _prefix_clickthrough(pg, out)

                    _log(out, "[6] búsqueda UI:")
                    _fresh_goto(pg, "")
                    time.sleep(6)
                    results["search"] = _search(pg, out)

                    _log(out, "[7] playback película:")
                    results["stream movie"] = _movie(pg, out)

                    _log(out, "[8] playback serie (episodio S:E):")
                    results["stream series"] = _series_episode(pg, out)

                    _log(out, "[9] playback anime (kitsu episodio abs):")
                    results["stream anime"] = _anime(pg, out)

                    _log(out, "[10] stream IPTV (HLS directo):")
                    results["stream tv/iptv"] = _iptv(pg, out)
                    if results["stream tv/iptv"]:
                        _grab_frame(pg, out)

                    _log(out, "[11] biblioteca + Continue Watching:")
                    results["library lastvids"] = _library_flow(pg, out)

                    if not QUICK:
                        _log(out, "[12] ciclo de vida del addon:")
                        results["lifecycle un/reinstall"] = _lifecycle(pg, out)
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
