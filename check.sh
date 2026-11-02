#!/bin/bash
# Smoke test del proxy: suite completa + endpoints + salud de fuentes.
# Uso: ./check.sh        (exit 0 = todo verde)
set -u
B=http://127.0.0.1:9700
fail=0
t() { # t <nombre> <condición>
  if [ "$2" = "$3" ]; then printf '  ok  %s\n' "$1"
  else printf ' FAIL %s (got %s, want %s)\n' "$1" "$2" "$3"; fail=1; fi
}
echo "== unit =="
python3 -m unittest discover -s test/unit -t . 2>&1 | tail -2
echo "== integration =="
python3 -m unittest discover -s test/integration -t . 2>&1 | tail -2
echo "== security =="
python3 -m unittest discover -s test/security -t . 2>&1 | tail -2
echo "== e2e (servicio en vivo :9700) =="
t manifest "$(curl -s -o /dev/null -w '%{http_code}' $B/manifest.json)" 200
t health   "$(curl -s -o /dev/null -w '%{http_code}' $B/health)" 200
t admin    "$(curl -s -o /dev/null -w '%{http_code}' $B/admin)" 200
t configure "$(curl -s -o /dev/null -w '%{http_code}' $B/configure)" 200
t streams  "$(curl -s -o /dev/null -w '%{http_code}' $B/stream/movie/tt0133093.json)" 200
t catalog  "$(curl -s -o /dev/null -w '%{http_code}' $B/catalog/movie/scinemeta_movie_estrenos.json)" 200
t search   "$(curl -s "$B/catalog/movie/suniform_movie_search/search=dune.json" | python3 -c 'import json,sys;print(len(json.load(sys.stdin).get("metas",[]))>0)' 2>/dev/null)" "True"
t calendar "$(curl -s "$B/catalog/series/scinemeta_series_estrenoscal/calendarVideosIds=kitsu:49847.json" | python3 -c 'import json,sys;print(len(json.load(sys.stdin).get("metasDetailed",[]))>0)' 2>/dev/null)" "True"
echo "== fuentes =="
curl -s $B/health | python3 -c '
import json, sys
d = json.load(sys.stdin)
up = sum(1 for h in d["sources"].values() if h.get("ok", 0) > 0)
print(f"  sources con >=1 fetch ok: {up}")
sys.exit(0 if up >= 10 else 1)' || fail=1
echo
[ $fail = 0 ] && echo "TODO VERDE" || echo "HAY FALLOS"
exit $fail
