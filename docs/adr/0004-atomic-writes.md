# ADR-0004: Escrituras de estado atómicas y durables

## Decisión
Todo fichero de estado (`config.json`, `.manifests-cache.json`, perfiles,
`.addon-state.json`) se escribe con `_atomic_json`: temp en el mismo
filesystem → flush → `os.fsync` → `os.replace` → fsync del directorio.

## Por qué
- `os.replace` en el mismo fs = rename atómico: un corte a mitad de write
  solo puede truncar el `.tmp`, nunca el fichero vivo.
- `fsync` antes del rename fija los datos; el fsync del directorio fija el
  rename mismo (sin él, un crash tras rename puede perder la entrada).
- En plataformas sin fsync de directorio se ignora (best-effort).

## Coste
~ms extra por write — irrelevante: estas escrituras son por evento de
usuario o por refresh (throttled), no por request.
