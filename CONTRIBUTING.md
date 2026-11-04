# Contribuir a conflux

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
python3 -m unittest discover -s tests/unit -t .    # rápido (~1s)
python3 -m unittest discover -s tests -t .         # suite completa (~40s)
scripts/check.sh                                   # linters + gates + smoke
```

El proyecto es stdlib-first (sin framework web): `src/conflux/` por capas
(`core` → `infra` → `domain` → `app` → `web` — solo se depende hacia abajo).
Imports internos absolutos: `from conflux.infra.net import fetch`.

## Reglas duras (ver AGENTS.md para el detalle)

- **Estado compartido**: nunca re-asignar nombres de `core/state.py`
  (`manifests[:] = ...`, `_M3U.clear()`); mutar in situ.
- **Escrituras de estado atómicas**: siempre `_atomic_json` (tmp+rename).
- **Tests herméticos**: NADA de red real fuera de `CONFLUX_E2E_LIVE=1`.
  Los e2e son VCR — tras tocar routing: `python3 scripts/record_e2e.py`.
- **Secretos**: `config.json` está gitignored; las claves aceptan `env:NOMBRE`.
  Nunca loguear keys — `log_ev` las enmascara (`***`).

## Política de bugs — failing-first

Un bug no se corrige por sospecha. Flujo obligatorio:

1. Reproducir y reducir al caso mínimo.
2. Test automatizado que **falla** en la versión afectada por la causa
   investigada (no por fixture roto ni timeout).
3. Corrección mínima — sin `except Exception: pass`, sin sleeps, sin subir
   timeouts para ocultar carreras.
4. El MISMO test pasa. Casos límite adyacentes. Suite completa.
5. El test queda como regresión en `tests/regression/` (o el archivo del
   módulo afectado).

## Tests

- `tests/unit/` — un archivo por módulo, fixtures reales, mocks locales.
- `tests/contract/` — forma de payloads contra el protocolo Stremio.
- `tests/security/` — XXE, traversal, redirects, secretos.
- `tests/resilience/` — upstreams degenerados → payload válido.
- `tests/property/` — hypothesis sobre parsers.
- `tests/e2e/` — servidor HTTP real en hilo, VCR hermético por defecto.

## Gates en CI (todos deben pasar)

ruff · pylint ≥9.5 · xenon avg A · mypy `check_untyped_defs` · bandit -ll ·
coverage ≥80% · schemathesis (sin 5xx) · pip-audit · detect-secrets.

Nueva feature = tests + entrada en `docs/openapi.yaml` (si es ruta) + bullet
en README Features + nota en AGENTS.md si hay gotcha operativa.
