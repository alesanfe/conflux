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
echo "== linters =="
if python3 -c 'import ruff' 2>/dev/null || python3 -m ruff --version >/dev/null 2>&1; then
  python3 -m ruff check src tests scripts >/dev/null 2>&1 && echo "  ok  ruff" || { echo " FAIL ruff"; fail=1; }
  python3 -m mypy >/dev/null 2>&1 && echo "  ok  mypy" || { echo " FAIL mypy"; fail=1; }
  python3 -m pylint src/conflux --reports=n --fail-under=9.5 >/dev/null 2>&1 && echo "  ok  pylint" || { echo " FAIL pylint"; fail=1; }
  python3 -m xenon --max-absolute D --max-modules C --max-average B src/conflux >/dev/null 2>&1 && echo "  ok  xenon" || { echo " FAIL xenon"; fail=1; }
else
  echo "  (herramientas no instaladas — pip install -r requirements-dev.txt)"
fi

echo "== unit =="
python3 -m unittest discover -s tests/unit -t . 2>&1 | tail -2
echo "== integration =="
python3 -m unittest discover -s tests/integration -t . 2>&1 | tail -2
echo "== security =="
python3 -m unittest discover -s tests/security -t . 2>&1 | tail -2
echo "== contract =="
python3 -m unittest discover -s tests/contract -t . 2>&1 | tail -2
echo "== property =="
python3 -m unittest discover -s tests/property -t . 2>&1 | tail -2
echo "== resilience =="
python3 -m unittest discover -s tests/resilience -t . 2>&1 | tail -2
echo "== regression =="
python3 -m unittest discover -s tests/regression -t . 2>&1 | tail -2
echo "== performance =="
python3 -m unittest discover -s tests/performance -t . 2>&1 | tail -2
echo "== coverage (umbral 80%) =="
if python3 -c 'import coverage' 2>/dev/null; then
  python3 -m coverage run -m unittest discover -s tests -t . >/dev/null 2>&1
  cov=$(python3 -m coverage report --include='src/conflux/*' | tail -1 | awk '{print $NF}' | tr -d '%')
  t "coverage ${cov}%" "$([ "${cov%.*}" -ge 80 ] && echo ok || echo bajo)" ok
else
  echo "  (coverage no instalado — pip install coverage)"
fi

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
