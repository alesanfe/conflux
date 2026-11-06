# Runbook — incidencias habituales

Diagnóstico primero: `conflux doctor` (o `--json`). Los errores llevan
código `CFX-*` y correlation id (`X-Request-Id` / `rid` en logs).

## El servicio no arranca
- `doctor` → `service` FAIL y `port` "ocupado": ya hay una instancia viva
  (el log registra `bind_fail`). Mata el proceso o usa otra instancia.
- Sin consola (exe): lee `conflux.log` junto al binario.

## Puerto ocupado (CFX-PORT-001)
`doctor` lo marca. En Windows `netstat -ano | findstr :9700`; POSIX
`ss -ltnp | grep 9700`.

## Addon no registrado (CFX-ADDON-001)
- `status` → `addon:` estado. `authentication_failed` = sin authKey:
  abre Stremio con sesión, o define `CONFLUX_STREMIO_AUTHKEY`, o usa el
  deep-link `stremio://127.0.0.1:9700/manifest.json`.
- `retry_pending`/`remote_unreachable`: `conflux addon` reintenta.

## Stremio no detectado / credencial inválida (CFX-AUTH-001/002)
- `doctor` → `authkey`: sin credencial o solo scraping. Activa sesión en la
  app o inyecta `stremio_authkey`/`CONFLUX_STREMIO_AUTHKEY`.
- `authentication_failed` tras cambio de cuenta: vuelve a `conflux addon`.

## Configuración corrupta (CFX-CFG-001)
`doctor` → `config` FAIL. `config.json` queda en defaults; revisa el
archivo (JSON válido, `config_version` ≤ actual). Copia `.bak` si existe.

## Fuente degradada / breaker abierto
`status` lista `breakers_open` y fuentes `degraded`. El breaker cierra solo
a los 60 s; streak ≥3 = fuente en cooldown (reintenta sola a los 15 min).

## Credencial de admin perdida
`admin_token` está en `config.json` (600). Rotar:
`conflux rotate-admin-token` — invalida la anterior al instante.

## Logs demasiado grandes
Rotan solos (5×2 MiB por defecto). Ubicación: `conflux status` → `log`.
Nivel: `--log-level=WARNING`, `CONFLUX_LOG_LEVEL` o `log_level` en config.

## Uso de memoria alto / picos bajo carga
Comportamiento conocido, no una fuga: las rutas pesadas (`/stream`,
`/subtitles`) hacen fan-out a ~30 upstreams y cada respuesta se bufferiza
entera (M3U/EPG de decenas de MB). Con concurrencia sostenida el pico de
memoria comprometida ronda varios GB; Python no devuelve arenas al SO,
así que el pico queda como piso. En uso real (1 usuario, 1-3 peticiones
concurrentes) la huella es muy inferior. Síntoma de presión real:
sondas `/health/live` con timeouts repetidos en ráfaga (>3 seguidos) —
el soak (`scripts/soak.py`) mide reposo-vs-reposo para distinguir
retención de churn transitorio. Techo de guarda: `CONFLUX_SOAK_RSS_CAP`.

## Desinstalación incompleta
`conflux uninstall` reintenta kill, autostart y desregistro. Restos:
`%LOCALAPPDATA%\Conflux` / `~/.local/share/conflux`, clave Run `Conflux`,
LaunchAgent `site.conflux.plist`, unit `conflux.service`, `autostart/
conflux.desktop`.

## Desactivar el scraping de credenciales
`authkey_scrape:false` en config o `CONFLUX_STREMIO_SCRAPE=0` — solo
env/config/keyring proveen el authKey.
