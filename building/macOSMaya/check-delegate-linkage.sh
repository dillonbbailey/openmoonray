#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# M1 exit checks (PLAN.md §8-9) on the installed delegate:
#   - arm64
#   - USD pinned to Maya's absolute path, never @rpath; no libusd_ndr
#   - no symbols from USD 22.11 headers
#   - no Python 3.9 and no external boost_python
#   - no RPATH into Maya's USD dir; every @rpath dependency resolves
#   - valid code signature
#   - the plugins load in mayapy with the right library copies (check-delegate.py)
#   - the TBB layout probe still passes
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

install="$MAYA_LOCAL_ROOT/install"
bins=(
    "$install/plugin/hd_moonray.dylib"
    "$install/plugin/hdMoonrayAdapters.dylib"
    "$install/plugin/moonrayShaderDiscovery.dylib"
    "$install/plugin/moonrayShaderParser.dylib"
    "$install/lib/libhydramoonray.dylib"
)
fail=0
bad() { echo "  FAIL: $*"; fail=1; }

maya_lib="$(cd "$MAYA_USD/lib" && pwd -P)"

for bin in "${bins[@]}"; do
    echo "$(basename "$bin")"
    [ -f "$bin" ] || { bad "missing"; continue; }
    ok=1
    fail_bin() { bad "$*"; ok=0; }

    archs="$(lipo -archs "$bin")"
    [ "$archs" = "arm64" ] || fail_bin "architectures '$archs', want arm64"

    id="$(otool -D "$bin" | tail -n 1)"
    deps="$(otool -L "$bin" | tail -n +2 | awk '{print $1}' | grep -vxF "$id" || true)"
    grep -q 'libusd_ndr'        <<<"$deps" && fail_bin "links libusd_ndr"
    grep -q 'Python3.framework' <<<"$deps" && fail_bin "links Python 3.9 (Python3.framework)"
    grep -q 'libboost_python'   <<<"$deps" && fail_bin "links external boost_python"
    # USD must be pinned to Maya's absolute path: deps holds USD 22.11 under
    # the same @rpath names (see hydra-only/cmake/PinMayaUsd.cmake).
    while read -r dep; do
        [ -n "$dep" ] || continue
        case "$dep" in
            */libusd_*) [[ "$dep" == "$maya_lib"/* ]] || fail_bin "USD not pinned to Maya: $dep" ;;
        esac
    done <<<"$deps"

    # Resolve @rpath deps the way dyld does: first LC_RPATH entry that has the
    # file wins. Absolute deps must exist.
    rpaths="$(otool -l "$bin" | awk '/cmd LC_RPATH/{getline; getline; print $2}' \
              | sed "s|@loader_path|$(dirname "$bin")|")"
    while read -r rp; do
        [ -n "$rp" ] || continue
        [[ "$rp" == "$maya_lib"* || "$rp" == "$PXR_SHIM"* ]] && fail_bin "RPATH into Maya/shim: $rp"
    done <<<"$rpaths"
    while read -r dep; do
        [ -n "$dep" ] || continue
        case "$dep" in
            @rpath/*)
                leaf="${dep#@rpath/}"; found=""
                while read -r rp; do
                    [ -n "$rp" ] && [ -e "$rp/$leaf" ] && { found="$rp/$leaf"; break; }
                done <<<"$rpaths"
                [ -n "$found" ] || fail_bin "unresolved $dep"
                # A library this build installs must resolve to that copy, not
                # a same-named one from the USD 22.11 core install.
                for own in "$install/lib/$leaf" "$install/plugin/$leaf"; do
                    [ -e "$own" ] && [ "$found" != "$own" ] && \
                        [ "$(cd "$(dirname "$found")" && pwd -P)/$leaf" != "$own" ] && \
                        fail_bin "$dep resolves to $found, not $own"
                done ;;
            @executable_path/*) ;;   # Maya's libpython; resolves inside Maya.app
            /usr/lib/*|/System/*) ;; # dyld shared cache; not on disk
            /*) [ -e "$dep" ] || fail_bin "missing $dep" ;;
        esac
    done <<<"$deps"

    # Undefined symbols are resolved at load time (-undefined dynamic_lookup
    # via Python::Module), so a TU compiled against deps' USD 22.11 headers
    # links cleanly. Its symbols carry the 22.11 namespace.
    old="$(nm -u "$bin" | grep -c 'pxrInternal_v0_22__' || true)"
    [ "$old" = 0 ] || fail_bin "$old undefined USD 22.11 (pxrInternal_v0_22) symbols"

    codesign --verify "$bin" 2>/dev/null || fail_bin "invalid code signature"

    usd="$(grep -c 'libusd_' <<<"$deps" || true)"
    [ "$ok" = 1 ] && echo "  ok: $archs, $usd USD libs pinned to Maya, all deps resolve, signed"
done

echo "mayapy load test:"
PYTHONPATH="$MAYA_USD/lib/python" "$MAYAPY" "$MOONRAY_MAYA_SCRIPTS/check-delegate.py" \
    "$install" "$INSTALL_DIR" "$DEPS_ROOT" 2>&1 | sed 's/^/  /' || fail=1

echo "TBB layout probe:"
bash "$MOONRAY_MAYA_SCRIPTS/tbb-probe/tbb-probe.sh" | tail -n 1

if [ "$fail" = 0 ]; then
    echo "RESULT: PASS"
else
    echo "RESULT: FAIL"
    exit 1
fi
