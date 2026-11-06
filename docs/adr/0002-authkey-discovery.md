# ADR-0002: Descubrimiento del authKey por cadena de proveedores

## Decisión
`find_auth()` devuelve `(key, proveedor)` probando en orden:
`CONFLUX_STREMIO_AUTHKEY` → `stremio_authkey` (config) → keyring del SO →
scraping acotado (leveldb, luego sqlite). El scraping es último recurso y se
puede desactivar (`authkey_scrape:false` o `CONFLUX_STREMIO_SCRAPE=0`).

## Límites del scraping
Solo directorios leveldb conocidos por SO, sin symlinks, ≤40 ficheros
recientes por dir, ≤64 MiB por fichero, presupuesto total 5 s, y los
candidatos se validan (`^[A-Za-z0-9+/=_-]{20,200}$`).

## Nunca en logs ni errores
Los errores de la API se pasan por `_safe_err` (la respuesta remota puede
hacer eco del payload). Solo se registra el proveedor usado, jamás la clave.

## Alternativas descartadas
- OAuth propio: requiere servidor público; el objetivo es zero-config local.
- Deep-link manual siempre: fricción alta para una app "que se monta sola".
