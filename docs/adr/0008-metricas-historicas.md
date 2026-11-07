# ADR-0008: Histórico de métricas por rollup diario

## Decisión
`.metrics-history.json` guarda rollups DIARIOS de los contadores globales
(requests ok/fail por upstream, streams servidos, pósters muertos, EPG) como
delta contra el último snapshot, en anillo de 90 días.

## Por qué
- Prometheus (`/metrics`) responde "cuánto ahora" — no sirve para "¿empeoró
  TorrentsDB esta semana?" ni para diagnosticar a posteriori sin un
  scraper externo corriendo 24/7, que un usuario doméstico no tiene.
- Deltas contra `_PREV`: los contadores son absolutos-desde-arranque; sin
  guardar el último snapshot, cada rollup duplicaría los acumulados.
- Un crash pierde como mucho el tramo desde el último snapshot (refresher
  30min + shutdown garantizado) — granularidad suficiente para tendencias.

## Qué NO es
No es una TSDB: no hay series por minuto ni por fuente (solo totales
globales por día). La visión por fuente en vivo está en `/health`,
`/metrics` y `/admin/diag` — el histórico existe para tendencias
(¿la red empeora?, ¿mueren más pósters?), no para forense fina.

## Consecuencias
- `/admin/diag` expone `history_30d` — un JSON pegable en un reporte sin
  exponer secretos (whitelist explícita de campos, QUAL-014).
- Escritura atómica como todo estado; un fallo de disco solo deja un log.
