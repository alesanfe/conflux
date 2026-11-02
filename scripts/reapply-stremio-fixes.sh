#!/usr/bin/env bash
# Re-aplica el parche PNA (Peario) en server.js tras cada update de Flatpak.
# Llamado por stremio-fix.path (vigila el archivo) y al login (oneshot).
SRV="$HOME/.local/share/flatpak/app/com.stremio.Stremio/x86_64/stable/active/files/libexec/stremio/server.js"
[ -f "$SRV" ] || exit 0
grep -q 'Access-Control-Allow-Private-Network' "$SRV" && exit 0

python3 - "$SRV" <<'PY'
import re, sys
p = sys.argv[1]
src = open(p).read()

# Rama OPTIONS: tras la cabecera Allow-Headers, añade Allow-Private-Network
pat_opt = r'(res\.setHeader\("Access-Control-Allow-Headers"[^\n]*\)),\s*'
if not re.search(pat_opt, src):
    print('anchor OPTIONS no encontrado — versión cambiada, parche no aplicado', file=sys.stderr)
    sys.exit(1)
src = re.sub(pat_opt, r'\1,\n        res.setHeader("Access-Control-Allow-Private-Network", "true"),\n        ', src, count=1)

# Rama normal: tras Allow-Origin "*", añade Allow-Private-Network
pat_get = r'(req\.headers\.origin && \(?res\.setHeader\("Access-Control-Allow-Origin", "\*"\))'
src = re.sub(pat_get, r'\1, res.setHeader("Access-Control-Allow-Private-Network", "true")', src, count=1)

open(p, 'w').write(src)
print('parche PNA reaplicado en', p)
PY
