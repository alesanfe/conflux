#!/usr/bin/env bash
# Smoke del binario empaquetado (CONFLUX-004..006): instala en HOME temporal,
# /health responde, desinstala, no quedan procesos ni ficheros.
# Uso: scripts/smoke_binary.sh dist/conflux[-linux|-macos|.exe]
set -u

BIN="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
HOME_T="$(mktemp -d)"
export HOME="$HOME_T" USERPROFILE="$HOME_T" XDG_DATA_HOME="$HOME_T/.xdg" \
       XDG_CONFIG_HOME="$HOME_T/.config" APPDATA="$HOME_T/.appdata" \
       LOCALAPPDATA="$HOME_T/.localappdata"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    # el exe es nativo: necesita rutas Windows, no MSYS (/tmp/...)
    WT="$(cygpath -w "$HOME_T")"
    export USERPROFILE="$WT" APPDATA="$WT\\.appdata" \
           LOCALAPPDATA="$WT\\.localappdata" ;;
esac
LOG="$HOME_T/smoke.log"

# timeout portable: los runners macOS no traen coreutils
_to() {
  local t="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$t" "$@"; else "$@"; fi
}

die() { echo "SMOKE FAIL: $*"; echo "--- log ---"; cat "$LOG" 2>/dev/null; exit 1; }

echo "bin: $BIN"
echo "home: $HOME_T"

# 1) instala+arranca (el binario se auto-instala y relanza; este proceso sale.
#    timeout por si el launcher no retorna — un crash no debe colgar el job)
_to 90 "$BIN" >"$LOG" 2>&1 || true

# 2) espera /health (manifests en frío tarda — 150s de margen)
OK=""
for _ in $(seq 1 150); do
  if curl -sf http://127.0.0.1:9700/health -o "$HOME_T/health.json" 2>/dev/null; then
    OK=1; break
  fi
  sleep 1
done
[ -n "$OK" ] || die "/health no respondió en 150s"
grep -q '"sources"' "$HOME_T/health.json" || die "schema /health incompleto"
echo "health: $(head -c 200 "$HOME_T/health.json")"

# 2b) endpoints adicionales del contrato
curl -sf http://127.0.0.1:9700/health/live >/dev/null || die "/health/live"
curl -sf http://127.0.0.1:9700/manifest.json | grep -q '"id"' \
  || die "/manifest.json inválido"

# 3) desinstalación (mata el servicio y limpia autostart+dir).
#    En Windows el borrado del propio exe es diferido (cmd ping+rmdir) y puede
#    tumbar el proceso en pleno exit → exit code no fiable; el gate son los
#    checks posteriores (puerto, procesos, directorio).
_to 60 "$BIN" uninstall >>"$LOG" 2>&1 || true

# windows: el borrado del dir es diferido (~5s ping+rmdir); esperar a que cierre
for _ in $(seq 1 20); do
  curl -sf http://127.0.0.1:9700/health >/dev/null 2>&1 || break
  sleep 1
done
sleep 6

# 4) nada sigue sirviendo
if curl -sf http://127.0.0.1:9700/health >/dev/null 2>&1; then
  die "el proxy sigue sirviendo tras uninstall"
fi

# 5) sin procesos huérfanos
if [ "$(uname -s)" = "Linux" ] || [ "$(uname -s)" = "Darwin" ]; then
  # -x: nombre de proceso exacto (regex), no cmdline — el propio script
  # lleva "conflux" en sus args y un pgrep -f daría falso positivo.
  pgrep -x 'conflux.*' >/dev/null 2>&1 && die "proceso conflux huérfano"
else
  tasklist 2>/dev/null | grep -qi conflux && die "proceso conflux.exe huérfano"
fi

# 6) sin directorio de datos
case "$(uname -s)" in
  Linux)   D="$HOME_T/.xdg/conflux" ;;
  Darwin)  D="$HOME_T/Library/Application Support/Conflux" ;;
  *)       D="$(cygpath "$LOCALAPPDATA")/Conflux" ;;
esac
[ -d "$D" ] && die "directorio de datos no eliminado: $D"

echo "SMOKE OK ($BIN)"
