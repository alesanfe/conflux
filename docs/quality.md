# Puertas de calidad (QUAL-015)

Mapa completo de las puertas automatizadas: qué corre cada una, cómo
lanzarla localmente y en qué workflow vive.

## Suite de tests

```bash
python3 -m unittest discover -s tests -t .   # ~1650 tests
python3 -m unittest discover -s tests/unit -t .        # solo unit (~10s)
scripts/check.sh                             # linters + suite + smoke
```

| Capa | Dir | Qué cubre |
|---|---|---|
| unit | `tests/unit/` | un archivo por módulo; anotaciones de mutantes (`Or@`, `Sub@`, `not-elim@`) documentan qué mata cada assert |
| integration | `tests/integration/` | flujos multi-módulo con `patch_fetch` global |
| security | `tests/security/` | XXE/defusedxml, path traversal, redirects, secretos en claro |
| contract | `tests/contract/` | payloads contra el protocolo Stremio (R3) |
| property | `tests/property/` | hypothesis: invariantes de parsers sobre entrada generada |
| resilience | `tests/resilience/` | upstreams degenerados (timeouts, JSON roto, HTML, vacíos) → payload válido (R5) |
| regression | `tests/regression/` | bugs históricos fijados con contexto |
| performance | `tests/performance/` | presupuestos: arranque, p95 rutas calientes, memoria estable, fan-out no serializado, parseos acotados |
| e2e | `tests/e2e/` | servidor HTTP real en hilo: todos los endpoints (VCR hermético en CI; `CONFLUX_E2E_LIVE=1` para upstreams reales) |

## Estáticos

| Herramienta | Gate | Config |
|---|---|---|
| ruff | 0 errores | `pyproject.toml` |
| pylint | ≥ 9.5 + `-E` limpio | `pyproject.toml` |
| xenon | complejidad media A | `pyproject.toml` |
| mypy | `check_untyped_defs` | `pyproject.toml` |
| bandit | `-ll` (medium+) | CI |
| detect-secrets | baseline | CI |
| pip-audit | deps sin CVE conocido | CI |
| zizmor + actionlint | workflows seguros | CI |
| CodeQL | análisis semanal | `codeql.yml` |

## Mutación

```bash
mutmut run        # config en pyproject.toml
```

`only_mutate`: `app/subs.py`, `domain/metafix.py`, `domain/lists.py` —
módulos con tests anotados para kill rate honesto (QUAL-001). mutmut copia
`src/`+`tests/` y muta solo esos archivos; la selección de tests cubre los
tres test-files correspondientes.

## Cobertura

Gate ≥ 80% en `scripts/check.sh` y CI (actual ~94%).

## Supply chain

- `requirements.txt` lock con hashes (`pip-compile` desde `.in`)
- Release: keyless cosign + `attest-build-provenance` + checksum verificado
  + attestation publicada (SLSA Build L3 — ver `supply-chain.md`)
- Actions fijadas por SHA; tag `v*` protegido; dependabot semanal

## Reglas operativas

- Escrituras de estado SIEMPRE `_atomic_json` (tmp+rename+fsync)
- Estado mutable compartido: mutar in situ, nunca rebindear
- Upstreams: best-effort — un fallo remoto no puede tumbar una request
- Tests deterministas: `CLOCK` fake, mocks locales, VCR en e2e
