#!/bin/sh
# Run every check: static checks, unit tests, then the end-to-end TUI tests.
#   sh tests/run_all.sh            everything
#   sh tests/run_all.sh quick      static checks + unit tests only (no tmux / gdb needed)
cd "$(dirname "$0")/.." || exit 1
PY=${PYTHON:-python3}
rc=0

if [ "$1" != "quick" ] && [ ! -x testapp/build/demo_debug ]; then
    echo "== building the test program"
    sh testapp/build_variants.sh || rc=1
    echo
fi

echo "== static checks"
sh tests/check_static.sh || rc=1

echo
echo "== unit tests"
$PY tests/test_units.py || rc=1

if [ "$1" != "quick" ]; then
    echo
    echo "== end-to-end TUI tests (tmux + gdb, takes a couple of minutes)"
    $PY tests/test_tui.py || rc=1
fi

echo
[ $rc = 0 ] && echo "ALL PASSED" || echo "SOME CHECKS FAILED"
exit $rc
