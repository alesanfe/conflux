#!/usr/bin/env bash
# setup.sh — monta el entorno completo del stremio-proxy en una máquina Ubuntu.
# Uso:  ./setup.sh                       (instala servicio + registra el addon en Stremio)
#       ./setup.sh --no-addon            (solo el proxy, sin tocar el perfil de Stremio)
#       ./setup.sh --env <backup.tar.gz> (restaura config/sources del backup ANTES de montar)
# También restaura automáticamente un dir env-backup/ descomprimido en el repo.
set -euo pipefail
cd "$(dirname "$0")/.."                    # raíz del repo

ADDON=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-addon) ADDON=0 ;;
        --env)      shift; ENV_BACKUP="$1" ;;
        *)          echo "flag desconocido: $1" >&2; exit 2 ;;
    esac
    shift
done

echo '== entorno (config/sources) =='
if [ -n "${ENV_BACKUP:-}" ]; then
    tar -xzf "$ENV_BACKUP" -C . && echo "restaurado desde $ENV_BACKUP:" && tar -tzf "$ENV_BACKUP" | sed 's/^/  /'
elif [ -d env-backup ]; then
    cp env-backup/* . 2>/dev/null && echo 'restaurado desde env-backup/'
else
    echo 'sin backup — defaults del repo'
fi

echo '== dependencias =='
python3 -c 'import requests' 2>/dev/null || {
    echo 'instalando python3-requests…'
    sudo apt-get update -qq && sudo apt-get install -y python3-requests
}
# defusedxml: opcional — torznab parsea XML del indexer (no confiable); sin él torznab queda off
python3 -c 'import defusedxml' 2>/dev/null || \
    sudo apt-get install -y -qq python3-defusedxml 2>/dev/null || true

echo '== servicio systemd (user) =='
mkdir -p ~/.config/systemd/user
sed "s|/home/alesanfe/apps/conflux|$PWD|;s|/home/alesanfe|$HOME|g" scripts/stremio-proxy.service > ~/.config/systemd/user/stremio-proxy.service
systemctl --user daemon-reload
systemctl --user enable --now stremio-proxy
sleep 2
curl -fsS --max-time 10 http://127.0.0.1:9700/health > /dev/null && echo 'proxy: OK (127.0.0.1:9700)'

if [ "$ADDON" = 1 ] && flatpak info com.stremio.Stremio &>/dev/null; then
    echo '== addon en Stremio =='
    python3 scripts/install_addon.py
elif ! flatpak info com.stremio.Stremio &>/dev/null; then
    echo 'Stremio no instalado (flatpak) — instálalo y corre: python3 scripts/install_addon.py'
fi

echo '== listo =='
echo 'Descubrir → cada pestaña muestra los catálogos unificados; Calendario → series/anime/estrenos.'
