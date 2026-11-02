# stremio-proxy

Addon agregador para Stremio en `http://127.0.0.1:9700`. Unifica ~24 addons upstream
(streams, metadatos, catálogos) detrás de un solo manifest con streams deduplicados,
ordenados por calidad/semillas, resolución de ids (kitsu/mal/tmdb → imdb), catálogos
agrupados con selectores, feed de calendario auto-poblado (series + anime de temporada
+ estrenos de cine) y resiliencia ante upstreams caídos.

## Instalar

```bash
# dependencia: python3 + requests (apt install python3-requests)
mkdir -p ~/.config/systemd/user
cp stremio-proxy.service ~/.config/systemd/user/
sed -i "s|/home/alesanfe|$HOME|" ~/.config/systemd/user/stremio-proxy.service
systemctl --user daemon-reload && systemctl --user enable --now stremio-proxy
curl localhost:9700/health
```

En Stremio: añade el addon `http://127.0.0.1:9700/manifest.json`.

## Configuración

- `sources.json` — lista de fuentes `[{"name","base"}]`; sobreescribe la embebida.
- `/health` — estado por fuente, rachas de fallos, cooldowns, fails por host y latencia por ruta.

## Test

```bash
python3 test_proxy.py   # 28 tests
```

Ver `AGENTS.md` para las reglas operativas (gotchas de localStorage UTF-16LE,
sincronización remota del perfil, semántica de ids, etc.).
