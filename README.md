# conflux

*A confluence of streams for Stremio.*

Addon agregador para Stremio en `http://127.0.0.1:9700`. Unifica ~24 addons upstream
(streams, metadatos, catálogos) detrás de un solo manifest con streams deduplicados,
ordenados por calidad/semillas, resolución de ids (kitsu/mal/tmdb → imdb), catálogos
agrupados con selectores, catálogo de estrenos (series + anime de temporada
+ estrenos de cine) y resiliencia ante upstreams caídos.

## Instalar como addon

El addon corre en tu máquina — GitHub aloja el código, no el servicio.

**Binario auto-instalable** (la opción más simple — sin Python):

Descarga de [Releases](../../releases) el de tu plataforma y ejecútalo —
`conflux.exe` (Windows), `conflux-linux` o `conflux-macos`. La primera vez se
copia a su carpeta de datos, registra el autostart (Run key / LaunchAgent /
systemd user), arranca en `:9700` y registra el addon en Stremio. Comandos:
`conflux install|uninstall|addon|serve` (`uninstall` lo desmonta todo).
Otros comandos: `conflux status [--json]` (resumen operativo), `conflux
doctor [--json]` (diagnóstico con códigos de salida 0-5), `conflux
rotate-admin-token` (rota el token de administración), `--log-level=NIVEL`
(DEBUG…CRITICAL; también `CONFLUX_LOG_LEVEL` o `log_level` en config.json).

`admin_token` se genera solo (256 bits) en el primer arranque y queda en
`config.json` — protege `/admin`, `/configure` y `POST /reload`. El `authKey`
de Stremio se descubre en este orden: `CONFLUX_STREMIO_AUTHKEY` →
`stremio_authkey` en config → keyring → scraping acotado del perfil local
(desactivable con `authkey_scrape: false` o `CONFLUX_STREMIO_SCRAPE=0`).

**Todo en uno** (Ubuntu + flatpak Stremio):

```bash
./scripts/setup.sh           # deps + servicio systemd + registro en tu perfil Stremio
```

**Todo en uno** (Windows):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_win.ps1
# python>=3.10 (o lo instala winget) + venv + deps + autostart en la carpeta
# Inicio + arranca el proxy + registra el addon en Stremio automáticamente
```

`python3 scripts/install_addon.py` registra el addon **sin clics**: extrae el
`authKey` del perfil de Stremio (leveldb en Windows/macOS/Linux, sqlite en
flatpak) y escribe la colección remota vía `api.strem.io` — aplica al reiniciar
la app. En flatpak también escribe el perfil local (`--kill` cierra la app).
Sin sesión/perfil usa el deep-link como fallback; `--remove` lo desinstala.

**Un click** — con el servicio corriendo, abre `http://127.0.0.1:9700/` y pulsa
«Instalar en Stremio». El deep link `stremio://` usa el Host de la petición:
funciona igual vía loopback, IP de LAN o dominio público (con `tls_cert`+`tls_key`
en config.json el link sale https).

**Manual** — Stremio → Addons → pega la URL del manifest:

```
http://127.0.0.1:9700/manifest.json
```

**Solo el servicio** (sin tocar el perfil de Stremio):

```bash
./scripts/setup.sh --no-addon
docker compose up -d            # alternativa: contenedor, puerto 9700
```

## Replicar este entorno en otra máquina

`config*.json` (claves, listas, M3U) y `sources.json` (fuentes+orden) están
gitignored — son machine-local. `env_backup.sh` los empaqueta y `setup.sh --env`
los restaura antes de montar servicio+addon:

```bash
# máquina origen
./scripts/env_backup.sh                    # → env-backup-<ts>.tar.gz

# máquina destino (Linux)
git clone <repo> && cd conflux
./scripts/setup.sh --env env-backup-*.tar.gz   # mismo entorno exactamente

# máquina destino (Windows)
powershell -ExecutionPolicy Bypass -File scripts\setup_win.ps1 -Env env-backup-*.tar.gz
```

## Configuración

- **`.env` (recomendado)** — copia `.env.example` a `.env`, rellena tus claves
  (debrid, Torznab, OpenSubtitles, MDBList, M3U, fuentes con token en
  `SOURCES_EXTRA`) y `setup_win.ps1`/`setup.sh` las vuelca a `config.json` +
  `sources.json` automáticamente. Para re-aplicar sin reinstalar:
  `python scripts/apply_env.py` + `POST /reload`. `.env` está gitignored.
  **Vía recomendada — instalador gráfico:** `conflux-setup.bat` lanza un
  wizard (tkinter, sin deps) que hace TODOS los pasos: entorno, Prowlarr,
  indexers, config, proxy+autostart y addon en Stremio.
  Alternativa headless: `scripts/setup_win.ps1`.
  Con `PROWLARR=1` se instala Prowlarr, conecta Torznab solo
  (extrae la ApiKey del config.xml) y **siembra indexers públicos por API**
  (`scripts/prowlarr_indexers.py` — lista personalizable con
  `PROWLARR_INDEXERS`; re-ejecutable, omite los existentes).
- **`/configure`** — UI web: fuentes on/off, debrid (RD/TorBox), ruta M3U,
  filtros de streams. Guarda en `config.json`.
- **`/admin`** — dashboard: salud por fuente, circuitos abiertos, latencia p50/p95/p99.
- `sources.json` — lista de fuentes `[{"name","base"}]`; sobreescribe la embebida.
  Aquí van también **fuentes con URL configurada** (token en la URL): Orion,
  MediaFusion, Comet, AIOStreams… — su `/configure` upstream genera la URL y
  se pega tal cual en `base`.
- `config.json` — `debrid_provider`/`debrid_key`, `m3u_path`,
  `stream_langs`, `sources_disabled`, `torznab_url`/`torznab_key`,
  `mdblist_key`, `catalog_lists`, `opensubtitles_key` (todas opcionales).
- `POST /reload` — aplica `config.json` en caliente y limpia cachés.
- `/health` — estado por fuente, fails por host y latencia por ruta.

## Features

- **Búsqueda unificada** — catálogo «Buscar todo» por tipo: fan-out a todos los
  upstreams que soportan `search`, dedup por id.
- **Estrenos** — catálogo sintético Cinemeta `year`+`genre=<año actual>` con
  picker de años; «Mis canales» M3U con EPG nativa.
- **Debrid** — 8 providers (RD, TorBox, AllDebrid, Premiumize, EasyDebrid,
  Debrid-Link, Offcloud, Put.io): marca ⚡ los cacheados (instantAvailability —
  Put.io lo detecta por transfers previos de la cuenta) y los sirve vía
  redirect `/r/<provider>/<hash>` → URL HTTP. `debrid_proxy: true` los sirve
  vía `/dp/` — túnel de salida estilo StremThru (el player no toca el debrid:
  la IP que ve es la del servidor — reenvía Range para seek).
  **Multi-debrid**: `debrid_providers: {"torbox": "k1", "real-debrid": "k2"}`
  consulta TODOS en paralelo y marca ⚡ con el provider que tenga el hash
  (la ruta `/r/<provider>/` ya lo resuelve); `debrid_provider`/`debrid_key`
  singular sigue funcionando.
- **Torznab** — `torznab_url`+`torznab_key`: Prowlarr/Jackett local como fuente
  extra de streams. **Y al revés**: `/torznab/api` (+alias `/newznab/api`)
  expone Conflux como indexer newznab para Sonarr/Radarr/Prowlarr —
  `?t=caps|search|movie|tvsearch&apikey=<torzapi_key>` devuelve RSS con los
  torrents agregados (magnet+trackers, o `/r/` debrid si está cacheado);
  `torzapi_key` vacía = abierto, o cae a `admin_token`.
- **Scrapers integrados** — `scrapers: ["yts","eztv","nyaa","tpb"]` (default
  todos): indexers públicos SIN addon upstream — YTS movie_details por imdb,
  EZTV get-torrents filtrado S/E, Nyaa RSS y TPB (apibay q.php) por título
  del meta. Cada uno es un
  worker propio del collect (aparece en `/health` y `contrib` separado).
  Además `scraper_rss: ["https://…/feed.rss"]` añade un worker `RSS:<host>`
  por feed RSS/Atom de indexer con magnets (torznab attrs / `<ns:infoHash>`
  incluidos) — cubre indexers sin scraper dedicado.
- **Source groups** — `source_groups: {"anime": ["Kitsu","Nyaa"]}`: routing
  condicional por tipo — ese tipo solo consulta las fuentes del grupo; los
  tipos sin grupo usan todas (estilo groups de AIOStreams). `[]` explícito
  apaga el tipo.
- **Concurrencia** — keep-alive + pool acotado: `max_connections` (default 64)
  limita hilos de request simultáneos; el resto espera en el backlog del
  kernel — un burst no crea hilos ilimitados. (Hilos con semáforo, no async.)
- **Observabilidad** — `/metrics`: exposición Prometheus —
  `conflux_source_requests_total`, `conflux_route_latency_ms`,
  `conflux_cache_entries`, `conflux_sources_total` — listo para
  `scrape_configs` de Grafana/Prometheus.
- **Zilean** — `zilean_url` (self-hosted, docker `ipromknight/zilean`+postgres):
  hashlists DebridMediaManager = torrents **ya cacheados en debrid**; se sirven
  como streams ⚡ sin coste por consulta — más instant-playables gratis.
- **Usenet / Newznab** — `newznab_url`+`newznab_key` (NZBHydra2, Easynews vía
  hydra): items NZB servidos vía `/nzb/` → TorBox usenet
  (`createusenetdownload`→`mylist`→`requestusenetdownload`). Requiere
  TorBox entre los providers configurados (multi-debrid incluido).
  Con `nntp_host`/`nntp_port`/`nntp_user`/`nntp_pass`/`nntp_ssl`/`nntp_connections`
  los items salen como **streams NNTP nativos** (`nzbUrl`+`servers`+
  `fileMustInclude` — los reproduce Stremio Desktop v5/Android TV, sin debrid).
  Y con `sabnzbd_url`+`sabnzbd_key` sale una tercera variante `SABnzbd`:
  `/sab/<b64>` envía el NZB por `addurl` y abre la UI del downloader (descarga,
  no playback — estilo MediaFusion).
  Stack self-hosted listo: `docker compose -f docker-compose.usenet.yml up -d`
  (Zilean:8181 + NZBHydra2:5076).
- **Easynews directo** — `easynews_user`/`easynews_pass`: búsqueda por título
  (del meta ya cargado) en Global Search 2.0; sus enlaces llevan descarga
  propia — `/en/` re-inyecta `user:pass@` en el redirect para no filtrar
  credenciales al manifest. Clips <5min y posts passwd/virus descartados.
- **Parental** — `parental: true`: fuera streams y canales M3U con etiquetas
  adultas inequívocas (borde de palabra — 'sex education' sobrevive).
- **Addons extra (marketplace-lite)** — `extra_sources`: URLs de cualquier
  addon Stremio de la comunidad (base o `.../manifest.json`) — entran como
  fuentes completas (streams+catálogos+meta) y aparecen en `/configure` como
  desactivables. Quitarlos los elimina al siguiente `/reload`.
- **Regional** — IndiaStreams (OTT India), DesiFlix (seriales/cine indio),
  LatinMovies (cine en español) y **MediaFusion** integrados como fuentes.
  MediaFusion se **auto-provisiona** al arrancar (`mediafusion: true` por
  defecto): el proxy cifra una config con sus catálogos regionales vía
  `/encrypt-user-data` de la instancia pública ElfHosted — 21 catálogos
  (tamil/hindi/malayalam/kannada/telugu/bangla/punjabi/english + live TV +
  búsqueda) sin cuenta ni pasos manuales. Cualquier otro addon con
  `/configure` propio (AIOStreams, Comet…) entra generando su URL y
  pegándola en `extra_sources`.
- **Preferidos** — `stream_preferred`: boost +300 en ranking (no filtro —
  cierra el trío exclude/required/preferred de AIOStreams); `source_timeout`
  ajusta el timeout por fuente (0 = 4s).
- **Listas externas** — `catalog_lists`: listas IMDb (`/list/ls*`)
  o `mdblist:ID` (con `mdblist_key`) aparecen como catálogos.
- **Perfiles** — `/p/<nombre>/manifest.json` usa `config.<nombre>.json`
  (fuentes/idiomas propios); cada perfil es un addon instalable.
- **Config en URL** — `/cfg/<b64json>/manifest.json` (estilo Torrentio):
  `/configure` genera la URL con idiomas/filtros/rpdb/debrid incrustados —
  cada URL es un addon distinto sin tocar `config.json`.
- **Filtros de stream** — `stream_exclude` (términos csv: `cam,camrip,hdts,ts,screener…`; ≤3 chars con borde de palabra, ≥4 substring)
  saca releases malos de la lista; `stream_required` es su complemento
  required (solo pasa lo que contiene algún término: `es,latino,remux`),
  estilo AIOStreams. **Regex**: `stream_exclude_regex`/`stream_required_regex`
  aceptan patrones completos (`(?i)\b(cam|ts)\b`, `\d{3,4}p.*ita`) — un patrón
  inválido se salta y queda logueado, no tumba el pipeline. `stream_max_gb` descarta
  por tamaño (un 4K de 80GB no siempre gana); `stream_prefetch: false`
  desactiva la precarga al abrir fichas. `rpdb_key` sirve
  posters con rating (ratingposterdb.com, gratis) estilo MediaFusion —
  `rpdb_style` elige el estilo (`poster-default`/`poster-textless`) y
  `rpdb_fallback` (default on) pide a RPDB el poster original si no tiene
  uno con rating para ese id (nunca un hueco sin imagen).
- **Ranking consciente de calidad** — `res>cached>_qscore>seeds(+ES)>size`:
  `_qscore` pondera rip (REMUX>BluRay>WEB-DL>HDTV), HDR (DV>HDR10+>HLG),
  codec (AV1>HEVC>AVC) y audio (Atmos>DTS:X>TrueHD). El tamaño puntúa por
  bandas (≤15GB neutro, 15–40GB leve, >40GB castigado — dentro de banda gana
  el mayor como proxy de bitrate): un REMUX de 80GB no aplasta a un WEB-DL
  de 8GB a igualdad de calidad. **Pase ligero**: si el top-3 son solo
  archivos >15GB, el mejor stream ≤15GB conocido sube al puesto 2 — opción
  viable para conexiones limitadas sin perder el #1 de máxima calidad
  (`stream_light: false` lo desactiva). `stream_sort` reordena
  el stack por perfil (`seeds,quality` pone seeds primero — sort-stacks de
  AIOStreams); `stream_format` es el formatter (`{res} {hdr} {codec} {audio}
  {lang} {rip} {title} {es}` → nombre del stream a tu gusto — separadores
  huérfanos por tokens vacíos se limpian solos); `stream_max_chars`
  (default 200, 0=off) corta líneas largas en el último separador — las
  tarjetas del cliente nunca renderizan una línea interminable.
- **Subtítulos** — con `opensubtitles_key`: recurso `subtitles` ES-first,
  redirect `/s/<file_id>` al CDN. La descarga exige cuenta de usuario
  (`opensubtitles_user`/`pass` — registro gratis en opensubtitles.com, cuota
  diaria propia); sin ella `/s/` responde 503/404 (la api key sola no basta).
- **M3U propio** — `m3u_path` → catálogo tv «Mis canales» + streams directos.
  Con `epg_url` (XMLTV público, default epgshare ES) el catálogo se declara guía
  nativa (`behaviorHints.epgProvider` + extra `date`) y Stremio muestra la
  parrilla de programación por canal (~50 canales con EPG en la lista ES).
- **Streams ES** — badge `ES` y boost de orden para `stream_langs` (configurable).
- **Salud de streams** — HEAD async a URLs http: las confirmadas caídas
  (4xx o 2 timeouts) salen de la lista 30min.

## Otros despliegues

Despliegue público: expón el puerto detrás de TLS (`tls_cert`/`tls_key` en
`config.json`) y fija `admin_token` — las páginas admin piden login (cookie
HttpOnly+SameSite=Strict, el token nunca va en la URL) y el landing sirve el
deep link https automáticamente. Las claves de `config.json` aceptan
referencias `env:NOMBRE` para no guardar secretos en el archivo, y las URLs
remotas configurables (`epg_url`, `M3U_URL`) bloquean destinos link-local
(metadata cloud) — LAN/loopback siguen permitidos para Prowlarr y NAS.

Rutas documentadas en `docs/openapi.yaml`.

## Test

```bash
python3 -m unittest discover -s tests -t .   # suite completa
scripts/check.sh        # smoke test: endpoints + fuentes vivas
```

Ver `AGENTS.md` para las reglas operativas (gotchas de localStorage UTF-16LE,
sincronización remota del perfil, semántica de ids, etc.).

## Seguridad

Para reportar una vulnerabilidad usa
[GitHub Security Advisories](https://github.com/alesanfe/conflux/security/advisories/new)
— **no abras un issue público**. Política completa en `SECURITY.md`;
modelo de amenazas en `docs/threat-model.md`, cadena de suministro en
`docs/supply-chain.md` (SLSA L3, verificación de firmas/SBOM), operación en
`docs/runbook.md` y decisiones en `docs/adr/`.
