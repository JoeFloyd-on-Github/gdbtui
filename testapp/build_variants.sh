#!/bin/sh
# Build several variants of the demo program (src/*.cpp) for testing gdbtui:
# debug / no debug, optimized, stripped, separate debug file, moved sources...
#
#   sh build_variants.sh            build everything into ./build
#   sh build_variants.sh clean      remove ./build
#   CXX=clang++ sh build_variants.sh
#
# Only needs a C++ compiler; strip/objcopy (binutils) are used when present.

cd "$(dirname "$0")" || exit 1
SRCS=$(ls src/*.cpp)
OUT=build
CXX=${CXX:-g++}
COMMON="-pthread -Isrc"
LIBS="-lrt"        # shm_open lives in librt on older glibc (harmless elsewhere)

if [ "$1" = "clean" ]; then
    rm -rf "$OUT"
    echo "removed $OUT"
    exit 0
fi

if ! command -v "$CXX" >/dev/null 2>&1; then
    echo "error: compiler '$CXX' not found (set CXX=...)" >&2
    exit 1
fi

# Older compilers (e.g. GCC 4.8) default to C++98; demo.cpp needs C++11.
STD=""
if ! echo 'int main(){auto x=nullptr;(void)x;}' | "$CXX" -x c++ -fsyntax-only - >/dev/null 2>&1; then
    STD="-std=c++11"
fi

mkdir -p "$OUT"
OK=0
FAIL=0

build() {   # build NAME DESCRIPTION FLAGS...
    name=$1
    desc=$2
    shift 2
    # shellcheck disable=SC2086
    if "$CXX" $STD "$@" $COMMON -o "$OUT/$name" $SRCS $LIBS 2>"$OUT/$name.log"; then
        rm -f "$OUT/$name.log"
        printf '  %-24s %s\n' "$name" "$desc"
        OK=$((OK + 1))
        return 0
    fi
    printf '  %-24s FAILED (see %s/%s.log)\n' "$name" "$OUT" "$name"
    FAIL=$((FAIL + 1))
    return 1
}

have() { command -v "$1" >/dev/null 2>&1; }

echo "Building variants of the demo ($(echo $SRCS | wc -w) source files in src/) with $CXX ${STD:+($STD)} into $OUT/ ..."
echo

build demo_debug        "-g -O0      full debug info, no optimization (best debugging)" -g -O0
build demo_debug_g3     "-g3 -O0     debug info + macro definitions"                    -g3 -O0
build demo_debug_O2     "-g -O2      debug info but optimized (<optimized out>, jumpy stepping)" -g -O2
build demo_nodebug      "-O0         no debug info: function/assembly breakpoints only" -O0
build demo_nodebug_O2   "-O2         no debug info, optimized"                          -O2
build demo_nopie        "-g -no-pie  debug info, fixed load address (not PIE)"          -g -O0 -no-pie
build demo_movedsrc     "-g          debug info pointing at /build/src (needs path substitution)" \
      -g -O0 "-fdebug-prefix-map=$(pwd)=/build/src"

if have strip; then
    if build demo_stripped "-O0 + strip  no symbols at all: assembly only, Ctrl+N stops at entry" -O0; then
        strip --strip-all "$OUT/demo_stripped"
    fi
    if build demo_debug_stripped "-g + strip --strip-debug: symbols kept, debug info removed" -g -O0; then
        strip --strip-debug "$OUT/demo_debug_stripped"
    fi
else
    echo "  (strip not found - skipping stripped variants)"
fi

if have objcopy && have strip; then
    if build demo_splitdebug "-g, debug info in demo_splitdebug.debug (gnu-debuglink)" -g -O0; then
        (
            cd "$OUT" &&
            objcopy --only-keep-debug demo_splitdebug demo_splitdebug.debug &&
            strip --strip-debug --strip-unneeded demo_splitdebug &&
            objcopy --add-gnu-debuglink=demo_splitdebug.debug demo_splitdebug
        ) || echo "  (objcopy step failed for demo_splitdebug)"
    fi
else
    echo "  (objcopy not found - skipping split-debug variant)"
fi

# Static linking needs static libstdc++/libc packages; often missing - that's fine.
build demo_static "-g -static  statically linked (no shared libraries)" -g -O0 -static 2>/dev/null ||
    echo "      (static libraries not installed - this variant is optional)"

echo
echo "$OK built, $FAIL failed."
echo
echo "Try them with gdbtui, e.g.:"
echo "  python3 ../gdbtui.py $OUT/demo_debug"
echo "  python3 ../gdbtui.py $OUT/demo_nodebug        (Ctrl+X -> Browse Functions)"
echo "  python3 ../gdbtui.py $OUT/demo_stripped       (Ctrl+N stops at the entry point)"
echo "  python3 ../gdbtui.py --syscalls $OUT/demo_debug  (shared memory + localhost threads, Syscalls tab)"
echo "  python3 ../gdbtui.py $OUT/demo_debug --quick   (start-up steps only, then exit)"
echo "  python3 ../gdbtui.py $OUT/demo_debug read     (type input in the Output tab)"
echo "  python3 ../gdbtui.py $OUT/demo_debug crash    (segfault -> exception popup)"
echo "  python3 ../gdbtui.py $OUT/demo_movedsrc       (Ctrl+X -> Source Path Substitution: /build/src $(pwd))"
