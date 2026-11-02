# stremio-proxy

Addon agregador para Stremio en `http://127.0.0.1:9700`. Unifica ~24 addons upstream
(streams, metadatos, catálogos) detrás de un solo manifest con streams deduplicados,
ordenados por calidad/semillas, resolución de ids (kitsu/mal/tmdb → imdb), catálogos
agrupados con selectores, feed de calendario auto-poblado (series + anime de temporada
+ estrenos de cine) y resiliencia ante upstreams caídos.

## Instalar

```bash
# dependencia: python3 + requests (apt install python3-requests)
mkdir -p ~/.config/systemd/user
cp stremio-proxy.service ~/.config/systemd/user/
sed -i "s|/home/alesanfe|$HOME|" ~/.config/systemd/user/stremio-proxy.service
systemctl --user daemon-reload && systemctl --user enable --now stremio-proxy
curl localhost:9700/health
```

En Stremio: añade el addon `http://127.0.0.1:9700/manifest.json`.

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
- **Debrid** — con `debrid_key`: marca ⚡ los hashes cacheados (instantAvailability)
  y los sirve vía redirect `/r/<provider>/<hash>` → URL HTTP.
- **M3U propio** — `m3u_path` → catálogo tv «Mis canales» + streams directos.
- **Streams ES** — badge `ES` y boost de orden para latino/castellano/VOSE.
- **Salud de streams** — HEAD async a URLs http: las confirmadas caídas
  (4xx o 2 timeouts) salen de la lista 30min.

## Test

```bash
python3 test_proxy.py   # 28 tests
./check.sh              # smoke test: endpoints + fuentes vivas
```

Ver `AGENTS.md` para las reglas operativas (gotchas de localStorage UTF-16LE,
sincronización remota del perfil, semántica de ids, etc.).
