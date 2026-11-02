# stremio-proxy — notas de proyecto

Proxy unificador de addons Stremio en `http://127.0.0.1:9700` (systemd user `stremio-proxy`).

## Comandos

```bash
python3 -m unittest discover -s tests -t .  # 289 tests (~35s): unit+integration+security+contract+property+resilience+regression+performance+e2e
python3 -m unittest discover -s tests/unit -t .       # solo unit (~1s)
scripts/check.sh                            # suite + smoke endpoints + fuentes
systemctl --user restart stremio-proxy      # tras editar src/conflux/

## Estructura (src-layout estándar PyPA)

Repo en `~/apps/conflux`:

- `src/conflux/`     paquete por capas (imports absolutos `from conflux.* import`)
- `proxy.py`         entry + fachada compat (inserta src/ en sys.path)
- `tests/`           unit/ integration/ security/ e2e/ + `fixtures/` (datos reales)
- `scripts/`         check.sh, setup.sh, install_addon.py, stremio-proxy.service
- `docs/`            openapi.yaml
- `pyproject.toml`   metadata PEP 621 + deps + config herramientas
- `requirements.txt` / `requirements-dev.txt`   deps pinned

- `tests/unit/`        un archivo por módulo (fixtures reales, mocks locales)
- `tests/integration/` flujos multi-módulo con `patch_fetch` global
- `tests/security/`    defusedxml/XXE, traversal, redirects, secretos
- `tests/contract/`    forma de payloads contra el protocolo Stremio (R3)
- `tests/property/`    hypothesis: invariantes de parsers sobre entrada generada
- `tests/resilience/`  matriz de upstreams degenerados → payload válido (R5)
- `tests/regression/`  bugs históricos fijados con su contexto
- `tests/performance/` presupuestos de latencia + fan-out concurrente
- `tests/e2e/`         servidor HTTP real en hilo: TODOS los endpoints

Sub-paquetes por capa — cada uno solo depende "hacia abajo":

- `conflux/core/`   `state.py` (CFG/cachés/HEALTH/COOLDOWN/pools/log — nunca re-asignar
  nombres compartidos; mutar in situ: `manifests[:] =`, `_M3U.clear()`; solo `cfg.RE_ES`
  se rebindea) + `cfg.py` (config.json + perfiles)
- `conflux/infra/`  adaptadores externos: `net.py` (sesiones+fetch), `trakt.py`,
  `anilist.py`, `wikidata.py`, `debrid.py`, `torznab.py` (Prowlarr/Jackett, defusedxml)
- `conflux/domain/` lógica Stremio: `text.py` (parseo streams, RANK), `sources.py`
  (manifests upstream, res_*, impedancia ids R1), `manifest.py` (contrato: `_UTYPE`/
  `_SYNTH`/`_YR_OPTS`/agrupación), `lists.py` (listas ext + M3U), `calendar.py`
  (estrenos sintéticos), `catalog.py` (routing /catalog), `metafix.py` (_fix_meta*)
- `conflux/app/`    casos de uso: `stream.py` (/stream: _collect* + url-health),
  `meta.py` (/meta race/fallback), `subs.py` (OpenSubtitles), `bg.py` (prefetch+refresher)
- `conflux/web/`    `pages.py` (HTML /configure /admin /health) + `server.py`
  (handler H + ROUTES + main())

Imports internos absolutos (`from conflux.infra.net import fetch`).
journalctl --user -u stremio-proxy -f    # [done] muestra contrib por fuente + fallos
curl -s 127.0.0.1:9700/health            # err/usable/streak por fuente
```

## Taxonomía de bugs (de los reales encontrados)

**R1 · Impedancia de IDs — parseo semántico, nunca posicional.**
Los ids stremio varían: `tt123:1:2` (S:E), `kitsu:49847:2` (episodio ABSOLUTO),
`mx:digits:slug`, `_onepace1`, `tmdb:550`. Nunca `split(':')[0]` para el prefijo:
los IMDb llevan `tt` pegado a dígitos → usar `_id_prefix()` (letras iniciales).
Episodio absoluto → `:S:E` vía `videos[]` del meta (autoridad upstream). `_resolve_sid`
traduce prefijos no-IMDb → `tt` usando `imdb_id` del meta de la fuente declarada en `_PREFIX_SRC`.

**R2 · El manifest miente — verificar datos, no declaraciones.**
Upstreams declaran catálogos vacíos, metas que dan 404, tipos falsos (anime como
`series`), `extraRequired` que no es requerido, imágenes 404. Samplear endpoints en
vivo; clasificar por contenido (`_ANIME_CAT`); verificar imágenes por HTTP, no por
presencia del campo. `metas:[]` ≠ roto: puede ser upstream muerto, params
requeridos, cuenta necesaria, contenido nuevo real o rate-limit — diagnosticar antes de quitar.

**R3 · El manifest es contrato — Stremio filtra en cliente.**
`types`/`idPrefixes`/`resources` se evalúan del lado cliente ANTES de pedir nada:
id no declarado → "No addons were requested" sin tocar el proxy. Declarar
exactamente la unión de prefijos/tipos que sirven los datos reales (auditar metas,
no manifests upstream). Al añadir fuentes con ids propios (`mx:`, `_onepace:`),
añadir su prefijo.

**R4 · Skip blando ≠ éxito — medir esperado vs real.**
`prefijo/tipo no soportado`, `manifest sin stream` son skips legítimos (salen en
`/health` pero no en `fallos`). Una fuente que SÍ cubre (tipo,prefijo) y aporta 0
de forma sistemática es sospechosa, no "sin contenido". El bug de series
(`tt0903747` como prefijo) fue invisible porque las películas funcionaban:
verificar SIEMPRE un episodio de serie tras tocar routing.

**R5 · Ambiente variable — transitorio ≠ roto.**
Cold-starts (Vercel/workers >10s → `refresh_manifests` reintenta a 20s), cuotas
(`_RE_LIMIT` → cooldown 12h), 429 de imgur a IPs datacenter, consent walls.
Reintentar lo transitorio, disyuntor lo persistente, cachear lo caro.
Los manifests se persisten en `.manifests-cache.json`: al arrancar se sirven
cacheados y se refrescan en background — reinicios casi sin ventana fría
("Load failed" en el cliente suele ser marca de una petición en esa ventana,
se recupera al re-entrar a la vista).

**R6 · Una verdad, N copias — sync explícito + claves completas.**
El manifest va embebido en el descriptor: tras cambiarlo hay que actualizar el
perfil SQLite (`ItemTable key='profile'`, **BLOB UTF-16LE** — escribir TEXT
UTF-8 rompe la app con "Serialization error: expected value at line 1 column 1";
escribir con `json.dumps(prof).encode('utf-16-le')` como bytes) Y la colección remota.
**El remoto manda**: con usuario logueado, `addonCollectionSet` pisa el perfil
local al login — un write solo-local se pierde siempre. Y **matar de verdad**
la app antes de escribir sqlite: `flatpak kill` + `pgrep` hasta 0 procesos, o
la app persiste su estado en memoria al cerrar y sobrescribe la edición.
(`api.strem.io/api/addonCollectionSet` con `authKey` del perfil). Las claves deben
codificar la tupla discriminadora `(fuente, tipo, id)` — el cid es
`s<fuente>_<tipo_upstream>_<id>` porque `tmdb.top` existe como movie Y series.

## Decisiones vigentes

- `catálogo.type='anime'` para catálogos movie/series con contenido anime
  (`_ANIME_CAT`); `_UTYPE[cid]` conserva el tipo real para la llamada upstream.
- `_NO_CATALOG`: fuentes cuyos catálogos son clones (Stravo⊂StravoII) o están
  rotos (Orion sin config) — siguen aportando streams.
- `_CAT_KEEP['AllIPTV']`: géneros + ES/EN/PT — el descriptor remoto rechaza >~140 cats.
- Cinemeta dentro del proxy (metas canónicos `tt:` + catálogos `Cinemeta · …`).
- `calendarVideosIds`/`lastVideosIds`: `handle_catalog` traduce ids no-`tt` a `tt`
  vía `_resolve_sid` (pool paralelo) antes de llamar a Cinemeta — animes guardados
  por Kitsu aparecen en calendario/"continuar viendo". **Ojo**: reconstruir el extra
  con `urlencode(kv, safe=',')` — sin `safe` las comas se codifican `%2C`→`%252C`
  (doble encoding) y upstream recibe 1 solo id mutilado → `metasDetailed:[null]`.
- `scinemeta_movie_estrenos` (en `_SYNTH`): catálogo sintético = cinemeta
  `year`+`genre=<año actual>` — incluye no-estrenados del año; picker de años.
- `scinemeta_series_estrenoscal`: estrenos de cine EN EL CALENDARIO — declarado
  `series` porque el calendario solo consulta catálogos series; devuelve metas
  movie con `videos` sintéticos cuyo `released` = fecha de estreno (Cinemeta
  year×5 páginas + TMDB meta→imdb_id, filtrado >hoy). El feed upstream es
  escaso (~1-5 títulos) — crece al acercarse estrenos.
- `scinemeta_series_lastvids`: Continue Watching — `lastVideosIds` requerido;
  la app envía los ids de la biblioteca, `_cat_xlate_ids` traduce no-tt→tt y
  se reenvía a Cinemeta `last-videos`. Los catálogos upstream de feed
  (`last-videos`, `calendar-videos`, `kitsu-anime-last-videos`) NUNCA se emiten
  como listas navegables: sin ids caen al mismo feed por defecto (duplicado
  entre sí y ruido en el picker) y como opción de `_GRP` no pueden recibir los
  extras. Lo mismo con `*_filter` upstream (StravoII): endpoint de filtro sin
  args = lista genérica redundante con las opciones del propio grupo.
  Regla en `_mf_one_cat`: `req ∩ _FEED_EXTRA` o `id endswith '_filter'` -> None.
- Cinemeta standalone NO se re-añade solo: su reaparición fue por editar el
  perfil SQLite con la app abierta (ésta lo persistía de vuelta al salir).
  **Siempre `flatpak kill com.stremio.Stremio` antes de tocar localstorage.**

## Verificación mínima tras tocar routing

```bash
curl -s '127.0.0.1:9700/stream/series/tt0944947%3A1%3A1.json'   # episodio tt → >200
curl -s '127.0.0.1:9700/stream/series/kitsu%3A49847%3A2.json'   # kitsu abs-ep → >100
curl -s '127.0.0.1:9700/stream/movie/tt0137523.json'            # película → >200
curl -s '127.0.0.1:9700/meta/series/mx%3A6429078990673560656%3Akagewani-gG7FoWufVE7.json'
```
