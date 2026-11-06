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
