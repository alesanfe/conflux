# stremio-proxy — notas de proyecto

Proxy unificador de addons Stremio en `http://127.0.0.1:9700` (Windows: autostart
vía `Conflux.lnk` -> `start-conflux.bat` -> `.venv\Scripts\pythonw.exe proxy.py`;
en Linux iba como systemd user `stremio-proxy`).

## Comandos

```bash
python3 -m unittest discover -s tests -t .  # 618 tests (~22s): unit+integration+security+contract+property+resilience+regression+performance+e2e
python3 -m unittest discover -s tests/unit -t .       # solo unit (~1s)
python3 scripts/test_times.py [N] [dir] [--all]      # timer+memoria por test, p50/p95/p99,
                                                     # agregado por paquete; --mem ranking memoria
python3 scripts/test_times.py --save base.json       # baseline; --compare base.json = regresiones
                                                     # (normaliza por factor de carga de la maquina)
python3 scripts/test_times.py --nomem                # sin tracemalloc (~40% mas rapido)
# HYP_EXAMPLES=200 python3 -m unittest discover -s tests/property -t .  # property exhaustivo
coverage run -m unittest discover -s tests -t . && python3 scripts/cov_gate.py [80]
# cov_gate: CADA archivo src/ >= umbral (no media) — actual ~96% total
python3 scripts/mutation.py <modulo.py> [tests.py] [--max N] [--timeout S]
# mutation local Windows-safe (mutmut 3.x no va en Windows; en CI/Linux mutmut
# run sigue valiendo). Operadores: cmp/binop/boolop/not/const/return. Kill-rate
# real: cada mutante corre los tests enfocados (mapa FOCUS en el script)
scripts/check.sh                            # linters + suite + smoke endpoints + fuentes
python3 -m mypy                             # type-check (config en pyproject)
# CI: .github/workflows/ci.yml — lint + suites herméticas + coverage>=80
# (e2e se salta en CI: CI=true; verifica upstreams reales, o CONFLUX_E2E_LIVE=1)
# config.admin_token != '' -> /admin,/configure,POST /reload,/configure exigen
# ?token= o header X-Admin-Token (compare_digest; vacío = abierto, loopback)
# requirements.txt = lock con hashes (pip-compile desde requirements.in)
# escrituras de ficheros de estado SIEMPRE via _atomic_json (tmp+rename):
# config.json, .manifests-cache.json — un corte a mitad de write no corrompe
# CI: matrix py3.11-3.13 + pip-audit + detect-secrets + SBOM + docker build
# e2e es VCR: replay de tests/fixtures/e2e_responses.json.gz (396 urls).
# Regenerar tras tocar routing: python3 scripts/record_e2e.py && gzip -9kf
# Live real: CONFLUX_E2E_LIVE=1 python3 -m unittest tests.e2e.test_server_e2e
# restart Windows: matar por cmdline y relanzar (ver R7 — nunca por nombre de proc)
#   Stop-Process pythonw con CommandLine like '*proxy.py*'; luego start-conflux.bat
systemctl --user restart stremio-proxy      # Linux: tras editar src/conflux/
./scripts/env_backup.sh                    # snapshot config*/sources.json → tar.gz
./scripts/setup.sh --env <tar.gz>          # otra máquina: mismo entorno exacto
scripts\setup_win.ps1                      # Windows: venv+deps+autostart (carpeta Inicio)+addon
python scripts/apply_env.py                # .env → config.json+sources.json (idempotente)
# .env (gitignored): DEBRID_*, TRAKT_*, TORZNAB_*, OPENSUBTITLES_KEY, MDBLIST_KEY,
# M3U_PATH, SOURCES_EXTRA="N|url;N2|url2", PROWLARR=1 (instala y autoconecta torznab)

## Estructura (src-layout estándar PyPA)

Repo (raíz del checkout — en Windows `C:\Users\alex0\conflux`):

- `src/conflux/`     paquete por capas (imports absolutos `from conflux.* import`)
- `proxy.py`         entry + fachada compat (inserta src/ en sys.path)
- `tests/`           unit/ integration/ security/ e2e/ + `fixtures/` (datos reales)
- `scripts/`         check.sh, setup.sh (--env restaura backup), setup_win.ps1 (Windows), apply_env.py (.env→config), install_addon.py, env_backup.sh, record_e2e.py, stremio-proxy.service
- `docs/`            openapi.yaml
- `pyproject.toml`   metadata PEP 621 + deps + config herramientas
- `requirements.in`/`requirements.txt`  deps directas → lock con hashes (pip-compile)
- `requirements-dev.txt`               herramientas dev pinned (coverage/mypy/hypothesis/mutmut)
- `.github/workflows/`  ci.yml (matrix SO×py: lint+tests+sbom+security+docker) / codeql.yml / scorecard.yml / release.yml (attestación+gh-release al taguear v*)
- `.github/dependabot.yml`  updates semanales pip+actions · `.pre-commit-config.yaml`  hooks ruff/mypy

- `tests/unit/`        un archivo por módulo (fixtures reales, mocks locales)
- `tests/integration/` flujos multi-módulo con `patch_fetch` global
- `tests/security/`    defusedxml/XXE, traversal, redirects, secretos
- `tests/contract/`    forma de payloads contra el protocolo Stremio (R3)
- `tests/property/`    hypothesis: invariantes de parsers sobre entrada generada
- `tests/resilience/`  matriz de upstreams degenerados → payload válido (R5)
- `tests/regression/`  bugs históricos fijados con su contexto
- `tests/performance/` presupuestos de latencia + fan-out concurrente
- `tests/e2e/`         servidor HTTP real en hilo: TODOS los endpoints
  (`test_server_e2e.py` contrato+interfaces; `test_perf_e2e.py` presupuestos
  de latencia por endpoint + concurrencia — mismo boot real, VCR o live)

Sub-paquetes por capa — cada uno solo depende "hacia abajo":

- `conflux/core/`   `state.py` (CFG/cachés/HEALTH/COOLDOWN/pools/log — nunca re-asignar
  nombres compartidos; mutar in situ: `manifests[:] =`, `_M3U.clear()`; solo `cfg.RE_ES`
  se rebindea) + `cfg.py` (config.json + perfiles)
- `conflux/infra/`  adaptadores externos: `net.py` (sesiones+fetch), `trakt.py`,
  `anilist.py`, `wikidata.py`, `debrid.py`, `torznab.py` (Prowlarr/Jackett, defusedxml),
  `zilean.py` (hashlists DMM self-hosted → streams `behaviorHints.cached`)
- `conflux/domain/` lógica Stremio: `text.py` (parseo streams, RANK), `sources.py`
  (manifests upstream, res_*, impedancia ids R1), `manifest.py` (contrato: `_UTYPE`/
  `_SYNTH`/`_YR_OPTS`/agrupación), `lists.py` (listas ext + M3U), `calendar.py`
  (estrenos sintéticos), `catalog.py` (routing /catalog), `metafix.py` (_fix_meta*)
- `conflux/app/`    casos de uso: `stream.py` (/stream: _collect* + url-health),
  `meta.py` (/meta race/fallback), `subs.py` (OpenSubtitles), `bg.py` (prefetch+refresher)
  · `_idmap` (sources): mapeo prefijo->imdb SIN TTL (estable; cap 400 vía _cset)
  · `_m3u_metas` (lists): re-parsea solo si cambia el contenido del .m3u
  · `?fi=` de /r/ se sanitiza a int en `_db_resolve` (malformado -> 0)
- `conflux/web/`    `pages.py` (HTML /configure /admin /health) + `server.py`
  (handler H + ROUTES + main())

Imports internos absolutos (`from conflux.infra.net import fetch`).
Get-Content conflux.log -Wait -Tail 40  # live; journalctl --user -u stremio-proxy en Linux
curl -s 127.0.0.1:9700/health            # err/usable/streak por fuente
```

**R7 · Windows: reiniciar de verdad — varios pythonw pueden convivir.**
`pythonw proxy.py` no lleva 'conflux' en su CommandLine → los kill por nombre
no lo alcanzan y el proceso viejo sigue en :9700 sirviendo código stale
(los nuevos mueren al bind sin rastro). Matar SIEMPRE por cmdline:
`Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | ? CommandLine -like '*proxy.py*'`
y comprobar el dueño del puerto: `Get-NetTCPConnection -LocalPort 9700 -State Listen`.
Si muere sin log, mirar `bind_fail` en conflux.log.

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
la app antes de escribir sqlite: `taskkill /IM stremio*.exe /F` (o `flatpak kill`
+ `pgrep` en Linux) hasta 0 procesos, o
la app persiste su estado en memoria al cerrar y sobrescribe la edición.
(`api.strem.io/api/addonCollectionSet` con `authKey` del perfil). Las claves deben
codificar la tupla discriminadora `(fuente, tipo, id)` — el cid es
`s<fuente>_<tipo_upstream>_<id>` porque `tmdb.top` existe como movie Y series.

- `time.time()` (no monotonic) en TODOS los TTLs: los dicts comparten reloj y
  tests siembran tuplas `(epoch, val)` — una mezcla monotonic/epoch haría
  edades negativas gigantes (fresh/stale para siempre). Los persistidos
  (epg fetched, trakt_expires) exigen epoch igualmente. Saltos NTP son
  raros y se autocurran por TTL.
- `_cfg_public(cfg)` redacta secretos para log/respuestas (`key|token|secret|
  pass|admin` -> `***`) — usar en todo volcado de CFG, nunca `CFG.copy()` crudo.
- `build_manifest` resetea `_SYNTH/_GRP/_UTYPE/_YR_OPTS/_REQ` bajo `_MF_LOCK`:
  los dicts de routing se reconstruyen por build — quien añada un dict global
  mutable derivado del build debe incluirlo en el reset.
- Caches con clave perfil-sensible: si una key de `/cfg/` o `/p/` cambia el
  resultado, va en la clave del cache (ver `estrenoscal`, collect `dis`) o se
  aplica post-cache sobre copia (rpdb, debrid, stream_langs re-score).
- dicts con crecimiento no acotado prohibidos: `_SESS<=64`, `_fails<=200
  (podados por HOST_CD)`, `_DB_OK<=5000`, `_rtime<=64`, `_URL_*<=2000`,
  `_PROFILES<=32`, `_wd_c<=400`, `_PROFILES_LOCK` para pop+setdefault.
- auth admin: `?token=` NO existe — `X-Admin-Token` (API) o cookie
  `conflux_auth` HttpOnly+SameSite=Strict vía POST /login; el token jamás va
  en URL (historial/Referer/logs).
- SSRF: URLs remotas configurables (`epg_url`, `M3U_URL`) pasan `_public_url`
  (http(s); bloquea link-local/metadata, multicast, reserved — RFC1918/loopback
  OK para Prowlarr/NAS) y los redirects se revalidan (`_ChkRedirect` en epg).
  Torznab es local por diseño — exento.
- secretos: `"env:VAR"` en config.json resuelve del entorno en `load_config`;
  `_CFG_ENV_REF` re-emite la ref en `_save_cfg` (nunca el valor resuelto).
- ranking: `res > cached > _qscore(rip+hdr+codec+audio) > seeds(+500 ES) > size`
  — los campos de `extract_fields` ordenan, no solo decoran; `_lang_rows`
  devuelve 6-tupla `(out,res,seeds,size,cached,qscore)`.

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
- Streams: ranking `res>cached>_qscore>seeds(+500 ES)>size` reordenable por
  `stream_sort` (whitelist res/cached/quality/seeds/size); filtros
  `stream_exclude`/`stream_required`; `stream_format` = template del name
  (`{res} {hdr} {codec} {audio} {lang} {rip} {title} {es}`, `.get` seguro).
- `debrid_proxy` → `_db_mark_one` emite `/dp/` en vez de `/r/`: `_db_stream`
  abre requests stream + reenvía Range/If-*; `_db_proxy` copia 64KB y solo
  headers Content-* — túnel de salida (IP del servidor, no la del cliente).
- `zilean_url` (self-hosted :8181) → worker `_collect_zl` paralelo en `_collect`
  (como torznab): `/dmm/filtered?ImdbId=&Season=&Episode=` shape tolerante.
- `extra_sources` → `_apply_extra_sources` en load_config: URLs de addons →
  `SOURCES[:] = _SOURCES_BASE + extras` IN SITU (identidad); nombre=host; la
  whitelist /cfg/ NO la admite (son URLs — igual que torznab_url).
- `stream_preferred` (+300 boost, `_pref_boost` sobre out) — el ES-boost ya va
  horneado en r[2] tras `_lang_rows`: OJO la tupla post-lang es 6-campos con
  r[5]=qscore (no f) — `_seed_score` solo aplica a `_rowkey` pre-lang.
- `easynews` query = título del `_meta_c` (ficha cargada antes que streams);
  `/en/` re-inyecta user:pass@ en el 302 — creds fuera del manifest.
- Put.io cached = `transfers/list` con btih en `source` y COMPLETED/SEEDING
  (no hay instant-check público). Offcloud: `/api/cloud`+`explore`+`/api/cache`.
- Disyuntor por host en `fetch()`: `_fails[host]=(strikes,ts)` cuenta fallos
  CONSECUTIVOS (éxito resetea); ≥HOST_OPEN (4) -> fail-fast HOST_CD (60s) sin
  tocar la red. Los hosts muertos (Fenix/Frost...) no cuestan timeout en
  refresh/búsqueda/meta/stream.
- `_collect` despierta `done` temprano: `Timer(2.0)` dispara si `pending<=3`
  (los últimos stragglers son típicamente fuentes muertas/cold-start). El
  bucle sigue para el caché completo — los waiters ven ~todo a los ~2s en
  vez del budget 3.2s. Torznab va como futuro paralelo (antes síncrono en
  `finally` retrasaba `done` hasta su timeout).
- Búsqueda: dedup por id CANÓNICO (`_search_key` — `_resolve_sid` solo para
  prefijos alien; tt es gratis). Kitsu:X y su tt resuelto no duplican.
- `_URL_BAD`/`_URL_OK` acotados (`_url_prune` a >2000).
- `torrent_health` (default on): pipeline por infoHash (`infra/torrent_health.py`,
  veredictos 6h en `_SEED`, persistidos `.seed-cache.json` atomic+throttle 60s):
  1) scrape UDP trackers públicos (tandas 20); 2) los 0-seed **con** hints
  `tracker:` en `sources` -> **announce** a sus propios trackers
  (`_announce_udp`/`_announce_http`, máx 6): swarm -> 99; >=2 responden sin
  swarm -> **-2** (announce-dead: si sus trackers no lo ven, el cliente tampoco
  puede unirse — se descarta aunque tenga hints); todo timeout -> 'unknown'
  (tracker caído != torrent muerto); 3) sin hints o announce-unknown ->
  **DHT get_peers** (bencode `_bdec/_benc`, bootstrap routers, budget 90s/
  worker; DHT sin respuesta = "no visto", nunca falso vivo). Regla: >0/99 vivo;
  -1 sin hints -> fuera; -1 con hints -> se mantiene pero al fondo; -2 -> fuera
  con o sin hints. Itera TODOS los items (los ausentes del scrape también se
  anuncian). Se aplica en ambos caminos de `handle_stream` (fresco y cacheado).
  `patch_fetch` neutraliza `_scrape_hashes`; en tests DHT/announce no corren.
- Cinemeta standalone NO se re-añade solo: su reaparición fue por editar el
  perfil SQLite con la app abierta (ésta lo persistía de vuelta al salir).
  **Siempre matar la app DE VERDAD antes de tocar localstorage** —
  `taskkill /IM stremio*.exe /F` en Windows, `flatpak kill` en Linux —
  o la app persiste su estado en memoria y sobrescribe la edición.

## Controles de calidad (todos en verde en CI)

- Linters: ruff · pylint ≥9.5 · xenon avg A · mypy `check_untyped_defs` · bandit -ll
- Cobertura ≥80% gate (scripts/check.sh y CI; actual ~94%)
- Auth opcional: `admin_token` en config.json protege /admin /configure POST /reload
  (`?token=` o `X-Admin-Token`, compare_digest); vacío = abierto (loopback)
- TLS opcional: `tls_cert`+`tls_key` en config.json → wrap_socket en main()
- Shutdown limpio: SIGTERM persiste `.manifests-cache.json` (ev `{"ev":"shutdown"}`)
- Mutation testing opt-in: `mutmut run` (config en pyproject — copia src/tests/proxy.py
  y muta solo `only_mutate`; subs.py: 168 mutantes, ~64% killed)
- e2e hermética: fixture VCR gzip; CI nunca toca la red
- Escrituras de estado atómicas (tmp+rename): config.json, .manifests-cache.json

## Verificación mínima tras tocar routing

```bash
curl -s '127.0.0.1:9700/stream/series/tt0944947%3A1%3A1.json'   # episodio tt → >200
curl -s '127.0.0.1:9700/stream/series/kitsu%3A49847%3A2.json'   # kitsu abs-ep → >100
curl -s '127.0.0.1:9700/stream/movie/tt0137523.json'            # película → >200
curl -s '127.0.0.1:9700/meta/series/mx%3A6429078990673560656%3Akagewani-gG7FoWufVE7.json'
```

## MediaFusion auto-provisionado

`mediafusion: true` (default) -> `infra/mediafusion.provision()` cifra una
config de catálogos regionales vía `POST {host}/encrypt-user-data` de la
instancia pública ElfHosted (sin cuenta — el secret_str va en la URL). Se
llama sync dentro de `refresh_manifests()` (ya background) ANTES de enumerar
SOURCES, y async desde `load_config` (maybe_provision) — R5: si se hiciera
solo async, `_load_manifests_cache` en main() puede pisar manifests[] tras
el append y la fuente queda en SOURCES sin manifest alineado. La instancia
pública NO sirve streams sin cuenta propia (0 aun con timeout largo):
aporta catálogos/metas regionales (tamil/hindi/telugu/kannada/malayalam/
bangla/punjabi + live TV, agrupados en 3 cats con ~700 géneros); streams
regionales reales los aporta DesiFlix.
