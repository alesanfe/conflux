FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proxy.py .
COPY src ./src

# config.json / sources.json / config.<perfil>.json se montan como volumen
# o se copian junto al código; el proxy los busca junto a proxy.py.
EXPOSE 9700
CMD ["python3", "proxy.py"]
