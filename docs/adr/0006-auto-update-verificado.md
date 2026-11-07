# ADR-0006: Auto-update verificado por hash + sigstore

## Decisión
El cliente se auto-actualiza (opt-out en builds congeladas) descargando el
release de GitHub, verificando `sha256(binario) == sha256 publicado` como
requisito duro y el bundle sigstore como endurecimiento opcional, y haciendo
staged atómico (`conflux.new.exe` + marcador + `.cmd` de swap).

## Por qué
- Windows no permite reemplazar un exe en ejecución → el swap debe ocurrir
  entre la muerte del proceso y el siguiente arranque; un `.cmd` helper es
  la pieza mínima sin dependencias.
- La cadena de confianza ya existía en el workflow (cosign keyless +
  attest-build-provenance): el cliente reutiliza los mismos artefactos en
  vez de inventar otro mecanismo de firma.
- Hash = requisito duro, firma = endurecimiento: la lib `sigstore` no puede
  ir en el requirements base (peso) — un update sin ella se instala con el
  mismo digest que CI firmó y queda marcado `sig: 'hash-only'` en el
  marcador y el log (degradado explícito, nunca silencioso).

## Consecuencias
- Un release sin el trío `exe + .sha256 + .sigstore.json` nunca se aplica.
- Todo fallo de la cadena solo produce `log_ev` — un update roto no puede
  tumbar la instalación en ejecución (fail-safe).
- Instalaciones desde fuente no se auto-actualizan (`sys.frozen` gate).
