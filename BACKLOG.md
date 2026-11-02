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
| 1.1 | **Feed real de estrenos vía Trakt API** — `/calendars/all/movies`, `/shows/premieres`, `/anticipated` sustituyen el apaño Cinemeta/TMDB | El calendario muestra estrenos a 2-3 meses vista con fecha exacta; sin client_secret, solo `trakt-api-key` (client_id público) | P0 | M |
| 1.2 | **Episodios anime con fecha real** — AniChart/AniList API (gratis, sin key) da `nextAiringEpisode.airingAt` exacto para cada anime de temporada | Cada anime de AniSeason/Kitsu muestra su próximo episodio en su día/hora real, no solo los que Cinemeta conoce | P0 | M |
| 1.3 | **Ventana rodante configurable** — `CALENDAR_MONTHS_AHEAD` en config decide cuántos meses futuros alimentar | Cambiar 1→3 meses refleja en Discover/Calendar sin tocar código | P1 | S |
| 1.4 | **"Mi calendario" personal** — con auth Trakt opcional, mezclar `/calendars/my/*` (watchlist propia) con el feed global | Con token Trakt del usuario, la vista prioriza lo suyo; sin token, sigue el feed global | P1 | M |
| 1.5 | **Notificaciones nativas** — `behaviorHints.newEpisodeNotifications` ya alimenta el panel de avisos; verificar que el feed sintético también dispara "nuevo episodio" | Al arrancar, el panel de notificaciones lista los episodios del día | P1 | S |

## Épica 2 — Configuración sin tocar archivos (paridad AIOStreams)

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 2.1 | **`/configure` web** — HTML servido por el propio proxy: toggles por fuente, orden drag&drop, toggles por catálogo, test de salud inline | Cambios guardan en `config.json` y aplican en caliente; sin reinicio | P0 | L |
| 2.2 | **`config.json` unificado** — sources.json + opciones (años ventana, orden streams, blacklist géneros) en un solo archivo versionable | Todo parámetro editable vive en config; defaults sensatos si falta | P0 | S |
| 2.3 | **Hot-reload de config** — watcher o `POST /reload` que recarga fuentes/manifests sin `systemctl restart` | Editar config → catálogos nuevos en el manifest en <2s | P1 | S |
| 2.4 | **Perfiles con nombre** — `config.<perfil>.json` + selector en `/configure`; la URL del manifest lleva `?profile=` | Dos perfiles (p.ej. "pelis" vs "niños" sin XXX) coexisten | P2 | M |

## Épica 3 — Streams (paridad MediaFusion/AIOStreams)

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 3.1 | **Resolución debrid** — RD/AllDebrid/TorBox/Premiumize: `infoHash`→URL HTTP instantánea vía API (config `DEBRID_*` env/config) | Con key válida, streams marcados `⚡ cached` primero y abren al instante; sin key, todo igual que hoy | P1 | L |
| 3.2 | **Check-cache debrid masivo** — `instantAvailability` por lote antes de ordenar | Los hashes cacheados rankean sobre P2P | P1 | M |
| 3.3 | **Subtítulos ES prioritarios** — reordenar/flaggear streams que indican subs ES/LATAM en nombre; integrar endpoint `subtitles` propio que consulte OpenSubtitles con español primero | Streams con subs ES aparecen etiquetados `ES` antes que el resto | P1 | M |
| 3.4 | **Health-check de streams en background** — HEAD a URLs HTTP periódico; streams muertos se auto-descartan | La lista no muestra streams que den 404/timeout | P2 | M |
| 3.5 | **Prowlarr/Jackett como fuente** — si el usuario corre indexers locales, consultar Torznab directo además de addons | Fuentes Torznab configurables rinden resultados como un addon más | P2 | L |

## Épica 4 — Descubrimiento

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 4.1 | **Búsqueda unificada** — `search=` fan-out a todos los catálogos con soporte + dedup por imdb_id | Buscar "one piece" devuelve resultados mezclados de todas las fuentes sin duplicar | P0 | M |
| 4.2 | **M3U/Xtream propio** — `channels.m3u` local parseado a catálogo `tv` con EPG básico si el playlist lo trae | Canales propios aparecen en pestaña TV junto a IPTV/AllIPTV | P1 | M |
| 4.3 | **Overlays de rating** — metahub/RPDB: nota IMDb/RT en `poster` de metas (config `POSTER_RATINGS`) | Portadas de Discover con badge de nota | P2 | S |
| 4.4 | **Listas IMDb/MDbList/Trakt como catálogos** — pegar URL de lista → catálogo nuevo | Una lista pública de IMDb/Trakt aparece como sección | P2 | M |
| 4.5 | **"Porque viste X"** — recomendaciones TMDB `/similar`/`/recommendations` desde la biblioteca local como catálogo sintético | Catálogo "Recomendados" que cambia con lo que ves | P2 | M |

## Épica 5 — Observabilidad y administración

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 5.1 | **Dashboard `/admin`** — página con estado por fuente, cooldowns, latencias p50/p99, peticiones/min y botón "probar fuente" | Diagnóstico completo sin journalctl | P1 | M |
| 5.2 | **Métricas de streams servidos** — contador por fuente/calidad en `/health` | Se ve qué fuente aporta realmente | P2 | S |
| 5.3 | **Tests de integración vivos** — job que pega rutas clave (manifest, catálogo, meta, stream, calendario) y alerta | `make check` detecta regresiones en <30s | P1 | S |
| 5.4 | **Versionado semver + changelog** — tags git + CHANGELOG.md | Cada release identificable | P2 | S |

## Épica 6 — Distribución

| # | Historia | Criterio de aceptación | Pri | Est |
|---|---|---|---|---|
| 6.1 | **Repo público en GitHub** — push con README EN/ES, screenshots, badge de tests | Clonable y montable con `./setup.sh` en máquina limpia | P1 | S |
| 6.2 | **Empaquetado** — `.desktop`/installer o Flatpak-sandbox-friendly; alternativa: docker-compose | Instalación de 1 comando para usuarios no-técnicos | P2 | M |
| 6.3 | **Documentación de la API interna** — OpenAPI mínimo de las rutas propias | Otro dev puede integrarse sin leer proxy.py | P2 | S |

---

## Lo que queda FUERA (decisiones tomadas)

- **Usenet/NZB**: servicios de pago no usados — reabrir si cambia
- **Multi-usuario cifrado en URL**: proxy personal en localhost — sin beneficio real
- **Telegram/Stalker/Xtream scraping**: fiabilidad/legalidad dudosa
- **Scrobble playback**: vive en el cliente (Stremio lo hace con Trakt nativo), no en un addon

## Orden sugerido de ejecución

1. **1.1 Trakt estrenos** + **1.2 AniList airing** → el calendario pasa de "apaño" a "mejor que ninguno"
2. **2.2 config.json** → **4.1 búsqueda unificada** → base barata de alto impacto
3. **2.1 /configure** + **5.1 /admin** → la experiencia "producto"
4. **3.x debrid** cuando haya cuenta → paridad funcional total
