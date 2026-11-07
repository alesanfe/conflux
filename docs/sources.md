# Inventario de fuentes e integraciones externas (EXT-003)

Estado al 2025-11. `SOURCES` vive en `src/conflux/core/state.py` y puede
sobreescribirse con `sources.json` (12-factor). La columna "tipo" es el
recurso principal que aporta cada fuente según su manifest real (R2/R3 —
verificado por `scripts/audit_prefixes.py`, no por declaraciones).

## Addons upstream (SOURCES)

| Fuente | Rol | Notas operativas |
|---|---|---|
| Cinemeta | meta + catálogos canónicos tt | Popular/New/Featured + calendario/last-videos vía extras feed |
| Torrentio | stream | `language=spanish` en la URL |
| TorrentsDB | stream | — |
| Peerflix | stream | — |
| Meteor | stream | anime |
| Ytztvio | stream | worker CF |
| NoTorrent | stream | — |
| Orion | stream | requiere URL con token (`sources.json`/`STREMIO_ORION_URL`); sin catálogo (`_NO_CATALOG`) |
| PublicDom | meta + stream | public domain |
| Kitsu | catálogo + meta anime | sin stream: skip gratis en `_collect` |
| YouTube | catálogo canales | metas `yt_id:`/`yt:` con fixes propios (avatar, descripción) |
| IPTV | catálogo tv | `ch_`/canales; salud de canal: cuarentena `_IPTV_BAD` (`.iptv-health.json`) |
| AllIPTV | catálogo tv | ids `ch_`, `mx`…; géneros ES/EN/PT recortados (`_CAT_KEEP`) |
| Stravo / StravoII / Hyper | stream + catálogo | Stravo⊂StravoII → `_NO_CATALOG` en Stravo |
| Fenix | stream | muerto histórico — el breaker por host lo hace gratis |
| AniSeason | catálogo anime | estático (github.io) |
| TorrentCat | catálogo | — |
| OnePace | meta + stream | `_onepace`/prefix propio; instancia koyeb alterna dormía |
| IMDBCat | catálogo | — |
| RTomatoes | catálogo | — |
| TMDB | catálogo + meta | elfhosted |

Prefijos declarados en nuestro manifest = unión de lo que sirven los datos
reales; `ch_` se añadió tras detectarlo en datos vivos de AllIPTV.
Los prefijos servidos directamente por su fuente (`tvdb`, `mx`, `dsx`,
`ch_`) no requieren traducción a IMDb — `audit_prefixes.py` los lista como
informativos, no como gap.

## Servicios externos configurables

| Servicio | Config | Uso |
|---|---|---|
| RatingPosterDB | `rpdb_key` (+`rpdb_style`, `rpdb_fallback`) | posters con rating en metas `tt:*` |
| Prowlarr/Jackett | `torznab_url`/`torznab_key` | indexers Torznab (defusedxml) |
| Newznab | `newznab_url`/`newznab_key` | usenet indexers → `/nzb/` |
| RealDebrid/AllDebrid/Premiumize/TorBox | `debrid_provider`/`debrid_key`/`debrid_providers` | resolución + redirect `/r/`, túnel `/dp/` |
| SABnzbd | `sabnzbd_url`/`sabnzbd_key` | `/sab/` addurl → UI del downloader |
| EasyNews | `easynews_user`/`easynews_pass` | `/en/` redirect con creds en el destino |
| OpenSubtitles | `opensubtitles_key`(+user/pass) | recurso `subtitles`, `/s/<file_id>` |
| MDBList | `mdblist_key` | ratings en metas |
| XMLTV EPG | `epg_url` | parrilla IPTV (`.epg-cache.json`, SSRF-guarded) |
| Orion | `sources.json`/`STREMIO_ORION_URL` | ver tabla addons |
| MediaFusion | `mediafusion` (default on) | fuente elástica opcional |
| GitHub Releases | `auto_update`, `update_channel` | auto-update verificado (ver supply-chain.md) |

## Persistencia local (no-confidencial)

| Archivo | Contenido | Política |
|---|---|---|
| `config.json` | configuración versionada | atómico; `env:VAR` para secretos |
| `.manifests-cache.json` | manifests upstream | atómico al shutdown |
| `.url-health.json` | veredictos HEAD de urls de stream | TTL 30min, anillo 2000 |
| `.poster-health.json` | veredictos HEAD de posters | idem |
| `.iptv-health.json` | cuarentena de canales IPTV | 6h oculto, re-prueba a 3h |
| `.seed-cache.json` | veredictos torrent (tracker/DHT) | 6h |
| `epg-cache.json` | parrilla XMLTV parseada | atómico |
| `.metrics-history.json` | rollup diario de contadores | anillo 90 días |
| `conflux.update.json` + `conflux.new.exe` | update staged | swap en próximo arranque |
