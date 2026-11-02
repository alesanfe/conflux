#!/usr/bin/env bash
# setup.sh — monta el entorno completo del stremio-proxy en una máquina Ubuntu.
# Uso:  ./setup.sh            (instala servicio + registra el addon en Stremio)
#       ./setup.sh --no-addon (solo el proxy, sin tocar el perfil de Stremio)
set -euo pipefail
cd "$(dirname "$0")"

echo '== dependencias =='
python3 -c 'import requests' 2>/dev/null || {
    echo 'instalando python3-requests…'
    sudo apt-get update -qq && sudo apt-get install -y python3-requests
}

echo '== servicio systemd (user) =='
mkdir -p ~/.config/systemd/user
sed "s|/home/alesanfe|$HOME|g" stremio-proxy.service > ~/.config/systemd/user/stremio-proxy.service
systemctl --user daemon-reload
systemctl --user enable --now stremio-proxy
sleep 2
curl -fsS --max-time 10 http://127.0.0.1:9700/health > /dev/null && echo 'proxy: OK (127.0.0.1:9700)'

if [ "${1:-}" != '--no-addon' ] && flatpak info com.stremio.Stremio &>/dev/null; then
    echo '== addon en Stremio =='
    if pgrep -f 'com.stremio.Stremio' > /dev/null; then
        echo '!! Stremio está abierto — ciérralo y reejecuta, o el cambio se pierde.'
        exit 1
    fi
    python3 install_addon.py
elif ! flatpak info com.stremio.Stremio &>/dev/null; then
    echo 'Stremio no instalado (flatpak) — instálalo y corre: python3 install_addon.py'
fi

echo '== listo =='
echo 'Descubrir → cada pestaña muestra los catálogos unificados; Calendario → series/anime/estrenos.'
