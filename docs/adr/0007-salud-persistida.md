# ADR-0007: Veredictos de salud persistidos en disco

## Decisión
Los veredictos de salud de URLs de stream, pósters, canales IPTV y torrents
(tres diccionarios `BAD/OK` + pendientes en memoria) se persisten en JSON
vía `health_load`/`health_save` en `core/state.py`, con load-at-import,
TTL/ventana por dominio y poda acotada.

## Por qué
- Sin persistencia, cada reinicio re-probaba ciegamente los mismos recursos
  muertos: ventana fría donde los streams/pósters rotos volvían a emitirse
  hasta el siguiente veredicto. El usuario percibía "flicker" de elementos
  rotos tras cada restart.
- El patrón existía ya para torrents (`.seed-cache.json`) — se generalizó
  como `health_load`/`health_save` compartidos en vez de tres
  implementaciones.

## Contrato común
- `BAD`: `url -> (strikes, ts)`; `OK`: `url -> ts`. Muerto = ≥2 strikes o
  4xx/5xx directo; cada dominio fija su ventana (30min stream/poster, 6h IPTV).
- Save throttled por archivo (no un fsync por veredicto); escritura siempre
  `_atomic_json` (tmp+rename+fsync) — un corte no corrompe el cache.
- Cota de memoria (2000 entradas, poda LRU por timestamp) — un catálogo
  gigante no puede inflar el proceso.
- Resucitación garantizada: los veredictos envejecen fuera de la ventana y
  el sweep los re-prueba — un falso positivo no condena para siempre.

## Consecuencia
Los archivos son cache local no-confidencial y están gitignored; en tests
los módulos cargan lo que haya en `_ROOT` — los tests que tocan el filtrado
limpian las estructuras en setUp (hermeticidad).
