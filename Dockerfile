FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir requests==2.34.1 defusedxml==0.7.1
COPY proxy.py .
COPY src ./src

# config.json / sources.json / config.<perfil>.json se montan como volumen
# o se copian junto al código; el proxy los busca junto a proxy.py.
EXPOSE 9700
CMD ["python3", "proxy.py"]
