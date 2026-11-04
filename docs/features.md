# Features avanzadas — referencia de configuración

Todas las claves viven en `config.json` (o `config.<perfil>.json`) y se aplican
en caliente con `POST /reload`. Los valores `env:NOMBRE` resuelven desde el
entorno — nunca guardes secretos en claro.

## Multi-debrid — `debrid_providers`

```json
"debrid_providers": {"torbox": "env:TB_KEY", "real-debrid": "env:RD_KEY"}
```

Todos los providers configurados se consultan **en paralelo** por hash; el
primero que lo tiene cacheado marca ⚡ con su nombre y la URL `/r/<provider>/`
lo resuelve con ESE provider. Compatible con el singular
`debrid_provider`+`debrid_key` (se fusionan). Provider desconocido → se
ignora. Usenet requiere `torbox` entre los providers (el resto no lo sirve).

| provider | torrents | usenet | notas |
|---|---|---|---|
| real-debrid | ✓ | — | instantAvailability |
| torbox | ✓ | ✓ | único que sirve NZB vía API |
| alldebrid | ✓ | — | |
| premiumize | ✓ | — | |
| easydebrid | ✓ | — | |
| debrid-link | ✓ | — | |
| offcloud | ✓ | — | |
| putio | ✓ | — | ⚡ = transfer previo de la cuenta |

## Filtros regex — `stream_exclude_regex` / `stream_required_regex`

```json
"stream_exclude_regex": ["(?i)\\b(cam|hdts|screener)\\b"],
"stream_required_regex": ["(?i)(español|latino|vose)"]
```

Evaluadas sobre `name`+`title`+`description` del stream. Complementan (no
reemplazan) a `stream_exclude`/`stream_required` de términos csv. Un patrón
que no compila se descarta y queda en el log (`regex_bad`) — nunca tumba el
collect.

## Indexer para *arr — `/torznab/api` y `/newznab/api`

Conflux actúa como indexer: Sonarr/Radarr/Prowlarr buscan aquí y reciben los
torrents AGREGADOS de todas las fuentes.

```
GET /torznab/api?t=caps&apikey=K
GET /torznab/api?t=movie&imdbid=tt0137523&apikey=K
GET /torznab/api?t=tvsearch&imdbid=tt0944947&season=1&ep=2&apikey=K
GET /torznab/api?t=search&q=dune&apikey=K
```

- `apikey` = `torzapi_key` del config; si está vacía, `admin_token` sirve;
  si ambos vacíos, abierto (loopback).
- Items: `<enclosure url="magnet:…">` + attrs newznab (`category`, `size`,
  `seeders`, `infohash`, `magneturl`). Torrent cacheado en debrid → link
  `/r/<provider>/<hash>`; item usenet → `/nzb/<b64>`.
- En Prowlarr: Add Indexer → Generic Newznab → URL `http://<host>:9700` ,
  API path `/torznab/api`, API key = `torzapi_key`.

## Scrapers integrados — `scrapers`

```json
"scrapers": ["yts", "eztv", "nyaa"]
```

Indexers públicos consultados directamente (sin addon upstream). `[]` los
apaga; nombre desconocido se ignora; `null` = todos. Cada uno es un worker
propio del collect (health + contrib separados, no puede tumbar el collect).

| scraper | tipo | query | filtro |
|---|---|---|---|
| yts | movie | `imdb_id` | API JSON, top 20 |
| eztv | series | `imdb_id` numérico | solo S/E del id pedido |
| nyaa | series/anime | título del meta | RSS `c=1_2` (anime EN) |

## Usenet nativo — `nntp_*`

```json
"nntp_host": "news.easynews.com", "nntp_port": 563,
"nntp_user": "env:EN_USER", "nntp_pass": "env:EN_PASS",
"nntp_ssl": true, "nntp_connections": 20
```

Con host+user+pass configurados, los items Newznab se emiten como **streams
NNTP nativos** de Stremio — `nzbUrl` (nuestro `/nzb/<b64>`), `servers`
(`nntps://user:pass@host:port/conns`) y `fileMustInclude` (regex de vídeo).
Los reproduce Stremio Desktop v5 / Android TV sin debrid. Sin las claves, los
items caen al flujo TorBox de siempre.

## Source groups — `source_groups`

```json
"source_groups": {"anime": ["Kitsu", "Nyaa", "AniSeason"],
                  "tv": ["IPTV", "AllIPTV"]}
```

Routing condicional: un tipo con grupo solo consulta esas fuentes (los demás
tipos usan todas). `[]` explícito apaga el tipo. Reduce ruido y latencia —
un collect de anime no despierta 30 fuentes de cine.

## Concurrencia — `max_connections`

`max_connections` (default 64): semáforo sobre hilos de request. Las
conexiones por encima esperan en el backlog del kernel (`request_queue_size`
64) en vez de crear hilos ilimitados — un burst de Stremio al abrir la app
ya no multiplica hilos. Sigue siendo threading + keep-alive HTTP/1.1; no es
una arquitectura async.

## Métricas — `/metrics`

Exposición Prometheus (`text/plain; version=0.0.4`):

```
conflux_source_requests_total{source="Torrentio",result="ok"} 123
conflux_source_requests_total{source="Torrentio",result="fail"} 2
conflux_source_cooldown_seconds{source="Fenix"} 40
conflux_streams_served_total{source="Torrentio"} 107
conflux_streams_byres_total{source="Torrentio",res="1080P"} 40
conflux_source_up 26
conflux_sources_total 34
conflux_route_latency_ms{route="/stream",quantile="0.95"} 800.0
conflux_host_fail_streak{host="torrentio.example"} 3
conflux_cache_entries{cache="meta"} 450
```

```yaml
scrape_configs:
  - job_name: conflux
    static_configs: [{targets: ['127.0.0.1:9700']}]
```
