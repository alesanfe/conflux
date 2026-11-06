# Threat model — Conflux

## Activos protegidos
- `admin_token` y `stremio_authkey` (credenciales locales).
- `config.json` (claves de debrid/indexers del usuario).
- Integridad del perfil de Stremio (colección de addons remota).
- Disponibilidad del proxy en `127.0.0.1:9700`.

## Límites de confianza
1. **Loopback**: solo escucha en 127.0.0.1 — la red no llega al handler.
2. **Procesos locales**: otro proceso del mismo usuario puede leer `config.json`
   (0600 POSIX) y llamar al proxy — por eso los endpoints admin exigen token.
3. **Upstreams remotos**: addons/indexers de terceros — se tratan como hostiles
   (defusedxml, límites de bytes, disyuntores, timeouts).
4. **Almacenamiento local de Stremio** (leveldb/sqlite): solo lectura, rutas
   conocidas, scraping acotado y desactivable.
5. **Pipeline de release**: GitHub Actions con SHA pinning, attestaciones,
   SBOM y firma verificable.

## STRIDE
| Amenaza | Mitigación |
|---|---|
| Spoofing | `X-Admin-Token`/cookie HttpOnly+SameSite=Strict; compare_digest; sin token en URL |
| Tampering | escrituras atómicas fsync+rename; config_version; schema de keys whitelist |
| Repudiation | correlation IDs (X-Request-Id), logs JSON estructurados |
| Info disclosure | redacción key/token/secret/pass en logs y respuestas; `Cache-Control: no-store`; token nunca en URL/logs |
| DoS | rate-limit /login (10/min), semáforo de conexiones, body ≤64KiB, disyuntor por host, timeouts |
| Elevation | scraping de credenciales restringido a rutas conocidas, sin symlinks, con presupuesto; admin por token |

## Riesgos aceptados
- Un proceso local *con lectura de config.json* puede extraer el token admin
  (modelo loopback estándar; mitigable con keyring si el usuario lo instala).
- Contenido de catálogos upstream es inherentemente no verificable — se filtra
  por contrato Stremio y listas de exclusión.
