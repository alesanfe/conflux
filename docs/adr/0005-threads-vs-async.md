# ADR-0005: Threads frente a async

## Decisión
Fan-out a ~30 upstreams con `ThreadPoolExecutor` (32 workers para requests,
12 para background/bulkhead) en lugar de asyncio.

## Por qué
- El workload es I/O-bound de red con respuestas opacas — threads + sesiones
  `requests` por host dan keep-alive y un modelo mental simple.
- Async exigiría migrar todo el stack (stdlib http.server, sqlite, leveldb)
  y las librerías de terceros no son async.
- GIL no es cuello: el tiempo lo dominan los timeouts upstream, no CPU.

## Bulkhead
Dos pools separados: un upstream degenerado que llene el pool de background
no puede matar la latencia de los requests de usuario.

## Consecuencia observada
`_collect` sirve a los ~2s con stragglers de ~10s en background — cumple el
presupuesto UX sin complejidad async.
