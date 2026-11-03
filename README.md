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

Si Stremio está abierto, `python3 scripts/install_addon.py --kill` lo cierra y
registra el addon (local SQLite + colección remota si hay sesión).

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

# máquina destino
git clone <repo> && cd conflux
./scripts/setup.sh --env env-backup-*.tar.gz   # mismo entorno exactamente
```

## Configuración

- **`/configure`** — UI web: fuentes on/off, Trakt client_id, debrid (RD/TorBox),
  ruta M3U, horizonte del calendario. Guarda en `config.json`.
- **`/admin`** — dashboard: salud por fuente, circuitos abiertos, latencia p50/p95/p99.
- `sources.json` — lista de fuentes `[{"name","base"}]`; sobreescribe la embebida.
- `config.json` — `trakt_client_id`, `debrid_provider`/`debrid_key`, `m3u_path`,
  `horizon_days`, `stream_langs`, `sources_disabled` (todas opcionales).
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
- **Torznab** — `torznab_url`+`torznab_key`: Prowlarr/Jackett local como fuente
  extra de streams.
- **Listas externas** — `catalog_lists`: URLs de listas Trakt, IMDb (`/list/ls*`)
  o `mdblist:ID` (con `mdblist_key`) aparecen como catálogos.
- **Perfiles** — `/p/<nombre>/manifest.json` usa `config.<nombre>.json`
  (fuentes/idiomas propios); cada perfil es un addon instalable.
- **Subtítulos** — con `opensubtitles_key`: recurso `subtitles` ES-first,
  redirect `/s/<file_id>` al CDN.
- **M3U propio** — `m3u_path` → catálogo tv «Mis canales» + streams directos.
- **Streams ES** — badge `ES` y boost de orden para `stream_langs` (configurable).
- **Salud de streams** — HEAD async a URLs http: las confirmadas caídas
  (4xx o 2 timeouts) salen de la lista 30min.

## Otros despliegues

Despliegue público: expón el puerto detrás de TLS (`tls_cert`/`tls_key` en
`config.json`) y fija `admin_token` — las páginas admin quedan protegidas y el
landing sirve el deep link https automáticamente.

Rutas documentadas en `docs/openapi.yaml`.

## Test

```bash
python3 -m unittest discover -s tests -t .   # suite completa
scripts/check.sh        # smoke test: endpoints + fuentes vivas
```

Ver `AGENTS.md` para las reglas operativas (gotchas de localStorage UTF-16LE,
sincronización remota del perfil, semántica de ids, etc.).
