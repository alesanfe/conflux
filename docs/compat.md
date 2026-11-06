# Compatibilidad — matriz real

## Sistemas operativos × arquitecturas

| Plataforma | Binario release | Autostart | Smoke en CI | Estado |
|---|---|---|---|---|
| Windows x86_64 | `conflux.exe` | Run key + exe diferido | ✅ release.yml | soportado |
| Linux x86_64 | `conflux-linux` | systemd user / XDG | ✅ release.yml | soportado |
| macOS x86_64+arm64* | `conflux-macos` | LaunchAgent | ✅ release.yml | soportado |
| Linux arm64 | — | — | — | no publicado (fuente funciona, sin binario) |
| Windows arm64 | — | — | — | no publicado |

\* el binario sale del runner `macos-latest` (arm64 en CI actual); rosetta en
Intel sin verificar — no se afirma soporte Intel nativo.

Regla: solo se marca "soportado" lo que corre el smoke real (instala →
/health → uninstall → limpio) en CI. Un build que compila pero no corre
no cuenta.

## Rutas y filesystems

- Espacios y Unicode: cubierto en `tests/resilience/test_paths.py`
  (instalación en `%LOCALAPPDATA%`/`~/.local/share` con cualquier carácter).
- Solo-lectura: escritura atómica falla limpio conservando el fichero
  anterior (`_atomic_json`); el servicio arranca igual con config por
  defecto.
- Symlinks: el scraping del authKey nunca sigue enlaces fuera de los dirs
  leveldb conocidos (`not islink`).
- Mayúsculas/minúsculas: rutas normalizadas con `_norm` antes de comparar
  (dest exe vs running exe) — sin falsos positivos en Windows.

## Upgrade / reinstall (CONFLUX-046)

- `install` sobre una instalación existente conserva `config.json`
  (upgrade seguro; test `test_install_dos_veces_conserva_config`).
- `uninstall` es idempotente: segunda ejecución = no-op sin error.
- Downgrade: no bloqueado explícitamente — el binario sustituye al
  anterior y la config se conserva; si el esquema cambiara hacia atrás,
  `config_version` rechaza versiones futuras al cargar (defaults).

## Versiones Python

3.11–3.13 en matriz CI (ubuntu ×3 + macos/windows 3.13). El binario
congela su propio runtime — la versión de Python del usuario es
irrelevante.
