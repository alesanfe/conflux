# Supply chain — qué garantiza la release

## Cadena de artefacto (release.yml)

Para cada binario (linux/macos/windows) y el sdist/wheel:

```
build → smoke test del binario → sha256 → SBOM CycloneDX
      → firma keyless del sha256 (cosign, verificada en el job)
      → attest-build-provenance → publicación en la release
```

El digest probado en el smoke **es el mismo** que se firma, atesta y publica
— no hay ventana donde el artefacto publicado difiera del verificado.

## SLSA — nivel real

| Requisito | Estado |
|---|---|
| Build en plataforma hospedada | ✅ GitHub-hosted runners |
| Provenance generada por el builder | ✅ `attest-build-provenance` (OIDC Sigstore) |
| Provenance no falsificable por el autor | ✅ firmada con identidad efímera del workflow |
| Aislamiento entre builds | ✅ un job por artefacto |
| Parámetros no manipulables (SLSA L4) | ❌ no se afirma |

**SLSA Build L3** (hermetic + provenance). No se declara L4: el build no es
bit-reproducible (PyInstaller embebe rutas/timestamps).

## Verificación por el usuario

```bash
# provenance (GitHub CLI)
gh attestation verify conflux.exe --repo alesanfe/conflux

# firma del checksum (cosign)
cosign verify-blob \
  --bundle conflux.exe.sha256.sigstore.json \
  --certificate-identity-regexp \
    "https://github.com/alesanfe/conflux/.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  conflux.exe.sha256
sha256sum -c conflux.exe.sha256
```

## Otras garantías

- `requirements.txt` = lock con hashes (`pip-compile`); instalación reproducible.
- Actions fijadas por SHA completo + `zizmor`/`actionlint` en CI.
- Tag `v*` protegido (ruleset: sin borrado ni rewrite) + rama protegida con
  checks obligatorios — una release solo sale de un árbol verificado.
- dependabot semanal (pip + actions); `pip-audit` en CI.

## Auto-update verificado (cliente)

`config.auto_update` controla un chequeo diario en el refresher
(`conflux/infra/updater.py`). En builds congeladas (`conflux.exe`) el
default es **ON** — el entorno se auto-mantiene; una instalación desde
fuente lo tiene OFF y se actualiza con git/pip. Un `false` explícito en
config.json siempre desactiva:

1. **Discovery**: GitHub Releases API → release más reciente del canal
   (`config.update_channel`: `stable` = solo no-prerelease; `beta` = prereleases
   incluidas). Tag parseado como semver (`vX.Y.Z[-pre]`); si el tag no es más
   nuevo que la versión en ejecución, no hay acción.
2. **Assets**: exige `conflux.exe`, `conflux.exe.sha256` y
   `conflux.exe.sha256.sigstore.json` — release incompleta → skip.
3. **Integridad dura**: `sha256(binario) == sha256 publicado`. Un mismatch
   aborta siempre (`update_rejected`) — nunca se staged un binario que no casa.
4. **Procedencia**: bundle sigstore verificado con la lib `sigstore` si está
   instalada (dependencia opcional, fuera del requirements base). Sin ella el
   update staged queda marcado `sig: 'hash-only'` — degradado explícito en el
   marcador y el log, nunca silencioso.
5. **Staged atómico**: `conflux.new.exe` + `conflux.update.json` (tag, sha256,
   estado de firma) + `conflux-apply-update.cmd` (espera a que el proceso
   muera, mueve el nuevo exe y se auto-borra). El swap lo lanza el handler
   SIGTERM — Windows no permite reemplazar un exe en ejecución.

Toda la cadena es fail-safe: un fallo en discovery/descarga/verificación solo
emite un `log_ev` (`update_skip`/`update_rejected`/`update_err`) — la
instalación en ejecución nunca se toca. Instalaciones desde fuente no se
auto-actualizan: se actualizan con git/pip.

```jsonc
// config.json — control explícito (los defaults ya cubren el caso común)
{"auto_update": false}          // desactiva en builds congeladas
{"update_channel": "beta"}      // early adopters: prereleases incluidas
```

## SmartScreen (ejecutable sin firma EV)

`conflux.exe` no lleva firma Authenticode — un certificado OV/EV cuesta
dinero y va ligado a una identidad legal. Windows SmartScreen puede mostrar
"Windows protected your PC" la primera vez: es esperado, no un fallo del
build. Mitigación honesta:

1. **Verificar antes de ejecutar** (lo que realmente garantiza el binario):
   `gh attestation verify conflux.exe --repo alesanfe/conflux` y/o
   `cosign verify-blob` + `sha256sum -c` — sección "Verificación por el
   usuario". Si el digest casa con el firmado por CI, el SmartScreen es
   ruido reputacional, no una amenaza.
2. **Alternativa sin binario**: instalar desde fuente
   (`git clone` + `scripts/setup.sh` o `pip install .`) — mismo código
   auditado, cero avisos.
3. "More info → Run anyway" una vez verificado el hash; Windows recuerda la
   decisión por archivo.

Una firma de código EV/OV queda como mejora futura documentada — no se
afirma cobertura que no existe.
