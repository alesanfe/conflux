# ADR-0001: Estado de ejecución agrupado en `AppContext`

## Decisión
`conflux.core.ctx.AppContext` agrupa los contenedores mutables de ejecución
(fuentes, manifests, salud, disyuntores, métricas, cachés, sesiones, cfg,
pools). `conflux.core.state` expone los objetos del contexto por defecto —
el composition root — preservando la mutación in-situ que el resto del
código usa (`manifests[:] =`, `CFG.clear()`).

## Por qué no inyección total ya
Los módulos comparten referencias a los mismos contenedores; re-asignar los
nombres de módulo rompería 50+ archivos y la suite. Este paso da el
artefacto central (contexto aislable/destruible) sin el big-bang refactor.

## Consecuencias
- Tests pueden crear `AppContext()` aislados (cubiertos en test_ctx.py).
- `shutdown_pools()` es idempotente con recovery por re-registro.
- Futuro: pasar `ctx` por parámetro a los handlers a medida que se toquen.
