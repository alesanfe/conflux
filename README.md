# conflux

*A confluence of streams for Stremio.*

Addon agregador para Stremio en `http://127.0.0.1:9700`. Unifica ~24 addons upstream
(streams, metadatos, catálogos) detrás de un solo manifest con streams deduplicados,
ordenados por calidad/semillas, resolución de ids (kitsu/mal/tmdb → imdb), catálogos
agrupados con selectores, feed de calendario auto-poblado (series + anime de temporada
+ estrenos de cine) y resiliencia ante upstreams caídos.

## Instalar como addon

El addon corre en tu máquina — GitHub aloja el código, no el servicio.

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
  (debrid, Trakt, Torznab, OpenSubtitles, MDBList, M3U, fuentes con token en
  `SOURCES_EXTRA`) y `setup_win.ps1`/`setup.sh` las vuelca a `config.json` +
  `sources.json` automáticamente. Para re-aplicar sin reinstalar:
  `python scripts/apply_env.py` + `POST /reload`. `.env` está gitignored.
  **Vía recomendada — instalador gráfico:** `conflux-setup.bat` lanza un
  wizard (tkinter, sin deps) que hace TODOS los pasos: entorno, Prowlarr,
  indexers, config, proxy+autostart, addon en Stremio y login Trakt con el
  código a pantalla completa. Alternativa headless: `scripts/setup_win.ps1`.
  Con `PROWLARR=1` se instala Prowlarr, conecta Torznab solo
  (extrae la ApiKey del config.xml) y **siembra indexers públicos por API**
  (`scripts/prowlarr_indexers.py` — lista personalizable con
  `PROWLARR_INDEXERS`; re-ejecutable, omite los existentes).
- **`/configure`** — UI web: fuentes on/off, Trakt client_id, debrid (RD/TorBox),
  ruta M3U, horizonte del calendario. Guarda en `config.json`.
- **`/admin`** — dashboard: salud por fuente, circuitos abiertos, latencia p50/p95/p99.
- `sources.json` — lista de fuentes `[{"name","base"}]`; sobreescribe la embebida.
  Aquí van también **fuentes con URL configurada** (token en la URL): Orion,
  MediaFusion, Comet, AIOStreams… — su `/configure` upstream genera la URL y
  se pega tal cual en `base`.
- `config.json` — `trakt_client_id`, `debrid_provider`/`debrid_key`, `m3u_path`,
  `stream_langs`, `sources_disabled`, `torznab_url`/`torznab_key`,
  `mdblist_key`, `catalog_lists`, `opensubtitles_key` (todas opcionales).
- `POST /reload` — aplica `config.json` en caliente y limpia cachés.
- `/health` — estado por fuente, fails por host y latencia por ruta.

## Features

- **Búsqueda unificada** — catálogo «Buscar todo» por tipo: fan-out a todos los
  upstreams que soportan `search`, dedup por id.
- **Calendario autónomo** — `metasDetailed` con episodios reales (Cinemeta/Kitsu/
  AniSeason) + AniList airing (fechas exactas) + Trakt anticipated (con key) +
  estrenos de cine TMDB; sin depender de la biblioteca.
- **Trakt personal** — `/auth/trakt` (device flow, una autorización) → mi
  calendario (episodios de series seguidas + watchlist) y catálogos
  «Recomendados · tu Trakt» (history → related). Token auto-renovado.
- **Debrid** — 6 providers (RD, TorBox, AllDebrid, Premiumize, EasyDebrid,
  Debrid-Link): marca ⚡ los cacheados (instantAvailability) y los sirve vía
  redirect `/r/<provider>/<hash>` → URL HTTP.
- **Trakt** — `TRAKT_CLIENT_ID`/`SECRET` en `.env` (par público incluido en
  `.env.example` — listo sin registrar app). `python scripts/trakt_auth.py`
  abre el login: cuenta trakt.tv gratuita → calendario personal y
  "continuar viendo" activos. `python scripts/trakt_sync.py` sube lo visto en
  Stremio al historial Trakt (bitmaps `watched` del localStorage →
  `/sync/history`, idempotente) → siembra las recomendaciones. **Automático**:
  el proxy lo lanza al arrancar y cada 30 min (subproceso aislado — sin
  intervención).
- **Torznab** — `torznab_url`+`torznab_key`: Prowlarr/Jackett local como fuente
  extra de streams.
- **Listas externas** — `catalog_lists`: URLs de listas Trakt, IMDb (`/list/ls*`)
  o `mdblist:ID` (con `mdblist_key`) aparecen como catálogos.
- **Perfiles** — `/p/<nombre>/manifest.json` usa `config.<nombre>.json`
  (fuentes/idiomas propios); cada perfil es un addon instalable.
- **Config en URL** — `/cfg/<b64json>/manifest.json` (estilo Torrentio):
  `/configure` genera la URL con idiomas/filtros/rpdb/debrid incrustados —
  cada URL es un addon distinto sin tocar `config.json`.
- **Filtros de stream** — `stream_exclude` (términos csv: `cam,camrip,hdts,ts,screener…`; ≤3 chars con borde de palabra, ≥4 substring)
  saca releases malos de la lista, estilo AIOStreams. `stream_max_gb` descarta
  por tamaño (un 4K de 80GB no siempre gana); `stream_prefetch: false`
  desactiva la precarga al abrir fichas. `rpdb_key` sirve
  posters con rating (ratingposterdb.com, gratis) estilo MediaFusion.
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
