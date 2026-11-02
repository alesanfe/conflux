FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir requests
COPY proxy.py .

# config.json / sources.json / config.<perfil>.json se montan como volumen
# o se copian junto al código; el proxy los busca junto a proxy.py.
EXPOSE 9700
CMD ["python3", "proxy.py"]
