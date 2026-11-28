FROM python:3.12-slim

WORKDIR /app

# En contenedor el loopback no es alcanzable desde fuera: bind en todas las
# interfaces por defecto (los endpoints admin ya exigen token). Override con
# `docker run -e CONFLUX_BIND=...` o bind_host en config.json.
ENV CONFLUX_BIND=0.0.0.0

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proxy.py .
COPY src ./src

# config.json / sources.json / config.<perfil>.json se montan como volumen
# o se copian junto al código; el proxy los busca junto a proxy.py.
EXPOSE 9700
CMD ["python3", "proxy.py"]
