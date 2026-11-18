#!/bin/sh
# Static checks for gdbtui.py:
#   1. it byte-compiles
#   2. it parses with Python 3.6 grammar (needs Python 3.8+ to run this check)
#   3. pyflakes (unused names, undefined names) - if installed
#   4. vermin (minimum Python version of the syntax and stdlib APIs used) - if installed
#   5. the paste installer is current and reproduces gdbtui.py byte for byte
# Usage: sh tests/check_static.sh
cd "$(dirname "$0")/.." || exit 1
PY=${PYTHON:-python3}
fail=0
step() { printf '%-58s' "$1"; }
ok() { echo "ok"; }
bad() { echo "FAILED"; fail=1; }

step "byte-compile gdbtui.py"
$PY -m py_compile gdbtui.py && ok || bad

step "parses as Python 3.6 (ast feature_version)"
$PY - <<'PY' && ok || bad
import ast, sys
if sys.version_info < (3, 8):
    print("(skipped: needs python 3.8+) ", end="")
else:
    ast.parse(open("gdbtui.py").read(), feature_version=(3, 6))
PY

step "pyflakes"
if $PY -m pyflakes --version >/dev/null 2>&1; then
    out=$($PY -m pyflakes gdbtui.py make_installer.py tests/*.py 2>&1)
    [ -z "$out" ] && ok || { bad; echo "$out"; }
else
    echo "skipped (pip install pyflakes)"
fi

step "vermin: minimum Python version <= 3.6"
if command -v vermin >/dev/null 2>&1; then
    out=$(vermin -t=3.6- --no-tips --violations gdbtui.py 2>&1)
    echo "$out" | grep -q "Minimum required versions: 3\.[0-6]$" && ok || { bad; echo "$out"; }
else
    echo "skipped (pip install vermin)"
fi

step "install_gdbtui.txt reproduces gdbtui.py"
tmp=$(mktemp -d)
if (cd "$tmp" && bash < "$OLDPWD/install_gdbtui.txt" >/dev/null 2>&1) && cmp -s "$tmp/gdbtui.py" gdbtui.py; then
    ok
else
    bad
    echo "  (regenerate with: python3 make_installer.py)"
fi
rm -rf "$tmp"

exit $fail
