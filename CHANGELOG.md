# Changelog

## [1.0.0] - 2026-11-02

Primera versión pública — **conflux**.

### Core
- Agregación de ~24 addons Stremio (streams, metas, catálogos) en un manifest único
- Dedup y ordenación uniforme de streams (calidad → semillas → caché)
- Resolución de ids kitsu/mal/anidb/tmdb → imdb, incluidos episodios absolutos
- Catálogos fusionados con selectores de género/año, dedup de nombres
- Resiliencia: keep-alive por host, reintentos, circuit breakers, caché de
  manifests en disco, prefetch en background

### Calendario (diferenciador)
- `metasDetailed` con episodios fechados reales (Cinemeta calendar-videos)
- Feed AniList airing (sin key) — hora exacta de emisión
- Feed Trakt `anticipated` + `/calendars/my/*` personal (OAuth device flow,
  token auto-renovado en `/auth/trakt`)
- Estrenos de cine TMDB con vídeo sintético = fecha de estreno
- `behaviorHints.newEpisodeNotifications`
- `horizon_days` configurable

### Streams
- Debrid Real-Debrid + TorBox: `instantAvailability` por lote, badge ⚡,
  redirect `/r/<p>/<hash>` → URL HTTP final
- Health-check async de URLs http (4xx o 2 timeouts = fuera 30min)
- Badge `ES` + boost para latino/castellano/VOSE (`stream_langs`)

### Descubrimiento
- «Buscar todo»: fan-out `search=` a todos los upstreams con dedup
- «Recomendados · tu Trakt»: history → related (con cuenta conectada)
- `m3u_path` → catálogo tv «Mis canales» + streams directos

### Administración
- `/configure` web: fuentes on/off, Trakt, debrid, M3U, horizonte, idiomas
- `/admin` dashboard: salud por fuente, circuitos, latencia p50/p95/p99
- `POST /reload` — config en caliente
- `config.json` unificado (gitignored — contiene tokens)
- `check.sh` smoke test + 28 tests unitarios
