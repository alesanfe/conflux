#!/bin/bash
# Smoke test del proxy: suite completa + endpoints + salud de fuentes.
# Uso: ./check.sh        (exit 0 = todo verde)
set -u
B=http://127.0.0.1:9700
fail=0
# Preferir el venv del repo (Windows: .venv/Scripts, POSIX: .venv/bin);
# el python3 global puede no tener las dev-deps.
if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python3 ]; then PY=.venv/bin/python3
else PY=python3; fi
t() { # t <nombre> <condición>
  if [ "$2" = "$3" ]; then printf '  ok  %s\n' "$1"
  else printf ' FAIL %s (got %s, want %s)\n' "$1" "$2" "$3"; fail=1; fi
}
suite() { # suite <dir>
  out=$("$PY" -m unittest discover -s "tests/$1" -t . 2>&1 | tail -2)
  echo "$out"
  echo "$out" | grep -qE '^OK' || fail=1
}
echo "== linters =="
if "$PY" -m ruff --version >/dev/null 2>&1; then
  "$PY" -m ruff check src tests scripts >/dev/null 2>&1 && echo "  ok  ruff" || { echo " FAIL ruff"; fail=1; }
  "$PY" -m mypy >/dev/null 2>&1 && echo "  ok  mypy" || { echo " FAIL mypy"; fail=1; }
  "$PY" -m pylint src/conflux --reports=n --fail-under=9.5 >/dev/null 2>&1 && echo "  ok  pylint" || { echo " FAIL pylint"; fail=1; }
  "$PY" -m xenon --max-absolute C --max-modules B --max-average B src/conflux >/dev/null 2>&1 && echo "  ok  xenon" || { echo " FAIL xenon"; fail=1; }
  "$PY" -m bandit -r src/conflux -ll -q >/dev/null 2>&1 && echo "  ok  bandit" || { echo " FAIL bandit"; fail=1; }
else
  echo "  (herramientas no instaladas — pip install -r requirements.txt; pip install -r requirements-dev.txt)"
fi

echo "== unit ==";         suite unit
echo "== integration ==";  suite integration
echo "== security ==";     suite security
echo "== contract ==";     suite contract
echo "== property ==";     suite property
echo "== resilience ==";   suite resilience
echo "== regression ==";   suite regression
echo "== performance ==";  suite performance
echo "== coverage (umbral 80% por archivo) =="
if "$PY" -c 'import coverage' 2>/dev/null; then
  "$PY" -m coverage run -m unittest discover -s tests -t . >/dev/null 2>&1 || fail=1
  "$PY" scripts/cov_gate.py || fail=1
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
t search   "$(curl -s "$B/catalog/movie/suniform_movie_search/search=dune.json" | "$PY" -c 'import json,sys;print(len(json.load(sys.stdin).get("metas",[]))>0)' 2>/dev/null)" "True"
t calendar "$(curl -s "$B/catalog/series/scinemeta_series_estrenoscal/calendarVideosIds=kitsu:49847.json" | "$PY" -c 'import json,sys;print(len(json.load(sys.stdin).get("metasDetailed",[]))>0)' 2>/dev/null)" "True"
echo "== fuentes =="
curl -s $B/health | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
up = sum(1 for h in d["sources"].values() if h.get("ok", 0) > 0)
print(f"  sources con >=1 fetch ok: {up}")
sys.exit(0 if up >= 10 else 1)' || fail=1
echo
[ $fail = 0 ] && echo "TODO VERDE" || echo "HAY FALLOS"
exit $fail
