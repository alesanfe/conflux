#!/usr/bin/env bash
# env_backup.sh — snapshot del entorno machine-local de conflux.
#
# Empaqueta los ficheros que definen ESTE entorno y están gitignored:
#   config.json / config.<perfil>.json   claves, fuentes off, listas, M3U…
#   sources.json                         lista de fuentes (orden + bases)
#   stremio-proxy.service (instalado)    unit systemd del usuario (referencia)
# Sale como env-backup-YYYYmmdd-HHMMSS.tar.gz en el repo (gitignored).
#
# Restaurar en otra máquina:
#   git clone … && cd conflux
#   ./scripts/setup.sh --env env-backup-*.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."

ts=$(date +%Y%m%d-%H%M%S)
out="env-backup-${ts}.tar.gz"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

n=0
for f in config.json config.*.json sources.json; do
    [ -f "$f" ] || continue
    cp "$f" "$tmp/"
    echo "  + $f"
    n=$((n + 1))
done
# unit instalado (referencia — setup.sh lo regenera con sed del $HOME nuevo)
if [ -f "$HOME/.config/systemd/user/stremio-proxy.service" ]; then
    cp "$HOME/.config/systemd/user/stremio-proxy.service" "$tmp/"
    echo '  + stremio-proxy.service (instalado)'
fi
[ "$n" -gt 0 ] || { echo 'nada que respaldar (sin config*/sources.json)'; exit 1; }

tar -czf "$out" -C "$tmp" .
echo "env backup: $out ($(du -h "$out" | cut -f1))"
echo 'restaurar: ./scripts/setup.sh --env '"$out"
