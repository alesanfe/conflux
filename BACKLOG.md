# Backlog — stremio-proxy

Objetivo: superar a AIOStreams, MediaFusion, StremThru y los addons Trakt
manteniendo la diferenciación que ninguno tiene: feed de calendario con
estrenos, resolución kitsu↔imdb, integración localStorage/perfil remoto,
normalización en español y cero configuración para el usuario local.

Prioridades: **P0** diferenciador inmediato · **P1** paridad+superación · **P2** madurez
Esfuerzo: **S** <1 sesión · **M** ~1-2 · **L** varias sesiones

---

## Épica 1 — Calendario y estrenos (la ventaja propia)

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 1.1 ✅ | **Feed real de estrenos vía Trakt API** — `/calendars/all/movies`, `/shows/premieres`, `/anticipated` sustituyen el apaño Cinemeta/TMDB | El calendario muestra estrenos a 2-3 meses vista con fecha exacta; sin client_secret, solo `trakt-api-key` (client_id público) | P0 | M |
| 1.2 ✅ | **Episodios anime con fecha real** — AniChart/AniList API (gratis, sin key) da `nextAiringEpisode.airingAt` exacto para cada anime de temporada | Cada anime de AniSeason/Kitsu muestra su próximo episodio en su día/hora real, no solo los que Cinemeta conoce | P0 | M |
| 1.3 ✅ | **Ventana rodante configurable** — `horizon_days` en config decide cuántos días futuros alimentar | Cambiar el valor refleja en Discover/Calendar sin tocar código | P1 | S |
| 1.4 ✅ | **"Mi calendario" personal** — Device Auth flow en `/auth/trakt` (una autorización en trakt.tv/activate, token auto-renovado); `/calendars/my/shows`+`movies` fusionados | Con token, la vista incluye lo tuyo marcado "en tu Trakt"; sin token, feed global | P1 | M |
| 1.5 ✅ | **Notificaciones nativas** — `behaviorHints.newEpisodeNotifications` ya alimenta el panel de avisos; verificar que el feed sintético también dispara "nuevo episodio" | Al arrancar, el panel de notificaciones lista los episodios del día | P1 | S |

## Épica 2 — Configuración sin tocar archivos (paridad AIOStreams)

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 2.1 ✅ | **`/configure` web** — HTML servido por el propio proxy: toggles por fuente, orden drag&drop, toggles por catálogo, test de salud inline | Cambios guardan en `config.json` y aplican en caliente; sin reinicio. *Hecho: checkboxes de fuentes + campos trakt/debrid/m3u/horizon/langs; pendiente drag&drop y toggles por catálogo* | P0 | L |
| 2.2 ✅ | **`config.json` unificado** — sources.json + opciones (años ventana, orden streams, blacklist géneros) en un solo archivo versionable | Todo parámetro editable vive en config; defaults sensatos si falta | P0 | S |
| 2.3 ✅ | **Hot-reload de config** — watcher o `POST /reload` que recarga fuentes/manifests sin `systemctl restart` | Editar config → catálogos nuevos en el manifest en <2s | P1 | S |
| 2.4 ✅ | **Perfiles con nombre** — `/p/<perfil>/manifest.json` usa `config.<perfil>.json` (fuentes, idiomas, catálogos propios) | Dos perfiles (p.ej. "pelis" vs "niños" sin XXX) coexisten | P2 | M |

## Épica 3 — Streams (paridad MediaFusion/AIOStreams)

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 3.1 ✅ | **Resolución debrid** — RD/TorBox/AllDebrid/Premiumize/EasyDebrid/Debrid-Link: `infoHash`→URL HTTP vía API; redirect `/r/<p>/<hash>` | Con key válida, streams ⚡ primero y abren al instante; sin key, todo igual que hoy | P1 | L |
| 3.2 ✅ | **Check-cache debrid masivo** — `instantAvailability` por lote antes de ordenar | Los hashes cacheados rankean sobre P2P | P1 | M |
| 3.3 ✅ | **Streams ES prioritarios** — flag `ES` + boost de orden para audio latino/castellano/VOSE (`stream_langs` en config) | Streams ES etiquetados antes que el resto. *Pendiente: endpoint subtitles OpenSubtitles* | P1 | M |
| 3.4 ✅ | **Health-check de streams HTTP** — HEAD async (máx 15/req, 3s): 4xx/5xx o 2 timeouts = fuera 30min | La lista no muestra streams http confirmados caídos | P2 | M |
| 3.5 ✅ | **Prowlarr/Jackett como fuente** — `torznab_url`+`torznab_key` en config → búsqueda por imdb en el collect de streams | Fuentes Torznab configurables rinden resultados como un addon más | P2 | L |

## Épica 4 — Descubrimiento

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 4.1 ✅ | **Búsqueda unificada** — `search=` fan-out a todos los catálogos con soporte + dedup por id | Buscar "one piece" devuelve resultados mezclados de todas las fuentes sin duplicar | P0 | M |
| 4.2 ✅ | **M3U propio** — `m3u_path` en config parsea playlist local a catálogo `tv` «Mis canales» + streams `m3u:N` | Canales propios aparecen en pestaña TV junto a IPTV/AllIPTV. *Pendiente: Xtream API + EPG* | P1 | M |
| 4.3 ❌ | **Overlays de rating** — RPDB es servicio de pago; metahub no hace overlays. Descartado | — | P2 | S |
| 4.4 ✅ | **Listas IMDb/MDbList/Trakt como catálogos** — `catalog_lists` con URLs → catálogo «☰ nombre» por tipo | Una lista pública de IMDb/Trakt aparece como sección | P2 | M |
| 4.5 ✅ | **"Porque viste X"** — historial Trakt (`/users/me/history`) → `/related` por item, catálogo «Recomendados · tu Trakt» movie+series | Con Trakt conectado, recomendaciones personales visibles; sin token, oculto | P2 | M |

## Épica 5 — Observabilidad y administración

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 5.1 ✅ | **Dashboard `/admin`** — página con estado por fuente, cooldowns, latencias p50/p95/p99 por ruta, fails por host | Diagnóstico completo sin journalctl. *Pendiente: botón "probar fuente" y req/min* | P1 | M |
| 5.2 ✅ | **Métricas de streams servidos** — `streams_served` por fuente/resolución en `/health` + tabla en `/admin` | Se ve qué fuente aporta realmente | P2 | S |
| 5.3 ✅ | **Tests de integración vivos** — `check.sh` pega rutas clave (manifest, catálogo, meta, stream, calendario, búsqueda) + fuentes vivas | `./check.sh` detecta regresiones en <30s | P1 | S |
| 5.4 ✅ | **Versionado semver + changelog** — tags git + CHANGELOG.md | v1.0.0 taggeada | P2 | S |

## Épica 6 — Distribución

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 6.1 ✅ | **Repo público en GitHub** — github.com/alesanfe/conflux, LICENSE MIT, v1.0.0 | Clonable y montable con `./setup.sh` en máquina limpia | P1 | S |
| 6.2 ✅ | **Empaquetado** — Dockerfile + docker-compose.yml | `docker compose up -d` | P2 | M |
| 6.3 ✅ | **Documentación de la API interna** — `openapi.yaml` con todas las rutas propias | Otro dev puede integrarse sin leer proxy.py | P2 | S |

---

## Lo que queda FUERA (decisiones tomadas)

- **Usenet/NZB**: servicios de pago no usados — reabrir si cambia
- **Ratings en posters (RPDB)**: solo de pago — fuera
- **Cualquier feature que dependa de un servicio de pago**: fuera
- **Multi-usuario cifrado en URL**: proxy personal en localhost — sin beneficio real
- **Telegram/Stalker/Xtream scraping**: fiabilidad/legalidad dudosa
- **Scrobble playback**: vive en el cliente (Stremio lo hace con Trakt nativo), no en un addon

## Orden sugerido de ejecución

1. **1.1 Trakt estrenos** + **1.2 AniList airing** → el calendario pasa de "apaño" a "mejor que ninguno"
2. **2.2 config.json** → **4.1 búsqueda unificada** → base barata de alto impacto
3. **2.1 /configure** + **5.1 /admin** → la experiencia "producto"
4. **3.x debrid** cuando haya cuenta → paridad funcional total
