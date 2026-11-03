#!/usr/bin/env python3
"""Sembrar indexers públicos en Prowlarr vía su API REST (sin UI).

Lee torznab_url/torznab_key de config.json (o acepta URL + key por argv),
lista los schemas Cardigann disponibles y da de alta los indexers públicos
seleccionados. Idempotente: los que ya existen se omiten.

Uso:
    python scripts/prowlarr_indexers.py                 # config.json local
    python scripts/prowlarr_indexers.py URL APIKEY      # explícito
    PROWLARR_INDEXERS="1337x,Nyaa.si" python scripts/prowlarr_indexers.py
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Indexers públicos por defecto: cobertura películas/series/anime sin
# credenciales. Los bloqueados por Cloudflare/ISP se guardan igual vía
# forceSave (Prowlarr reintenta) — quitarlos de la lista si molestan.
DEFAULT_INDEXERS = (
    # generales (películas/series)
    "1337x", "The Pirate Bay", "EZTV", "LimeTorrents", "YTS", "TorrentDownload",
    "FileMood", "Knaben", "Torrent9", "TorrentKitty", "Catorrent",
    "World-torrent", "torrent-pirat", "NoNaMe Club", "RinTor.NeT", "U3C3",
    "TorrentProject2", "MagnetDownload", "Magnet Cat", "sosulki",
    # anime
    "Nyaa.si", "SubsPlease", "Anime Tosho", "Tokyo Toshokan", "ACG.RIP",
    "Anibt", "Bangumi Moe", "Mikan", "dmhy", "nekoBT", "Shana Project",
    # juegos/software/otros
    "Byrutor", "PC-torrent", "Internet Archive",
)


def _cfg_key():
    """(base_url, apikey) desde config.json del repo o argv."""
    if len(sys.argv) >= 3:
        return sys.argv[1].rstrip("/"), sys.argv[2]
    cfg_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "config.json")
    cfg = json.load(open(cfg_p, encoding="utf-8"))
    turl, key = cfg.get("torznab_url", ""), cfg.get("torznab_key", "")
    if not turl or not key:
        sys.exit("config.json sin torznab_url/torznab_key — define TORZNAB_* en .env o PROWLARR=1")
    p = urllib.parse.urlparse(turl)
    return f"{p.scheme}://{p.netloc}", key


def _req(method, base, key, path, payload=None, timeout=30):
    sep = "&" if "?" in path else "?"
    url = f"{base}/api/v1{path}{sep}apikey={key}"
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, e.read()[:400].decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)[:120]


def main():
    base, key = _cfg_key()
    wanted = [s.strip() for s in os.environ.get(
        "PROWLARR_INDEXERS", ",".join(DEFAULT_INDEXERS)).split(",") if s.strip()]

    st, existing = _req("GET", base, key, "/indexer")
    if st != 200:
        sys.exit(f"Prowlarr no responde en {base} ({st}) — ¿está arrancado?")
    have = {(i.get("name") or "").lower() for i in existing}

    st, app = _req("GET", base, key, "/appprofile")
    app_id = app[0]["id"] if st == 200 and app else 1
    st, schemas = _req("GET", base, key, "/indexer/schema", timeout=60)
    if st != 200:
        sys.exit(f"no se pudieron listar schemas ({st})")
    norm = lambda s: "".join(c for c in s.lower() if c.isalnum())
    by_name = {}
    for s in schemas:
        for k in (s.get("definitionName"), s.get("name")):
            if k:
                by_name.setdefault(norm(k), s)

    added, skipped, failed = [], [], []
    for name in wanted:
        if name.lower() in have or norm(name) in {norm(h) for h in have}:
            skipped.append(name)
            continue
        schema = by_name.get(norm(name))
        if not schema:
            cand = [s.get("name") for s in schemas
                    if s.get("name") and norm(name) in norm(s["name"])]
            failed.append(f"{name} (no existe; similares: {', '.join(cand[:3]) or 'ninguno'})")
            continue
        urls = schema.get("indexerUrls") or [""]
        ok = untested = False
        err = "?"
        for base_url in urls:  # el dominio por defecto puede estar caído: probar mirrors
            body = dict(schema)
            body["name"] = name
            body["appProfileId"] = app_id
            body.setdefault("tags", [])
            body.setdefault("priority", 25)
            for f in body.get("fields", []):
                if f.get("name") == "baseUrl":
                    f["value"] = base_url
            st, res = _req("POST", base, key, "/indexer", body, timeout=60)
            if st in (200, 201):
                ok = True
                break
            err = res[0].get("errorMessage", "?") if isinstance(res, list) else str(res)[:120]
        if not ok:  # test falló en todos los mirrors → guardar igual, Prowlarr reintenta
            body = dict(schema)
            body["name"] = name
            body["appProfileId"] = app_id
            body.setdefault("tags", [])
            body.setdefault("priority", 25)
            for f in body.get("fields", []):
                if f.get("name") == "baseUrl":
                    f["value"] = urls[0]
            st, _ = _req("POST", base, key, "/indexer?forceSave=true", body, timeout=60)
            untested = st in (200, 201)
        if ok:
            added.append(name)
        elif untested:
            added.append(f"{name} (sin test — puede estar caído)")
        else:
            failed.append(f"{name} ({err})")

    print(f"indexers: {len(added)} añadidos | {len(skipped)} ya existían | {len(failed)} fallidos")
    for n in added:
        print(f"  + {n}")
    for n in failed:
        print(f"  ! {n}")


if __name__ == "__main__":
    main()
