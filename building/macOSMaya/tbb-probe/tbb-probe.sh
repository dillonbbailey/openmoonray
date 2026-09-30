#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# M0 TBB sizeof probe (PLAN.md §7 Risk 1). Builds tbb-probe.cc twice:
#
#   A  "delegate"  pxr/ from $PXR_SHIM + TBB 2020.3 and boost from deps +
#                  scene_rdl2. This is the header set the delegate build will
#                  use. Also runs USD and scene_rdl2 code in one process.
#   B  "maya"      pxr/ + oneTBB 2022 from the full devkit, i.e. the headers
#                  Maya's own libusd_* were compiled with.
#
# Passes when every "must" line matches (see tbb-probe.cc for what that covers).
# Writes to $MAYA_LOCAL_ROOT/tbb-probe.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"

src="$MOONRAY_MAYA_SCRIPTS/tbb-probe/tbb-probe.cc"
out="$MAYA_LOCAL_ROOT/tbb-probe"
mkdir -p "$out"
[ -f "$PXR_SHIM/pxrConfig.cmake" ] || { echo "run unpack-usd-devkit.sh first" >&2; exit 1; }

# scene_rdl2's install omits common/arm/ (emulation.h, sse2neon.h, avx2neon.h),
# which common/math/Math.h includes on arm64. Borrow it from the source tree.
mkdir -p "$out/include/scene_rdl2/common"
ln -sfn "$MOONRAY_SOURCE_ROOT/moonray/scene_rdl2/lib/common/arm" "$out/include/scene_rdl2/common/arm"

common=(
    -std=c++17 -O0 -arch arm64
    -DPXR_BOOST_PYTHON_NO_PY_SIGNATURES
    -isystem "$MAYA_PYTHON_FRAMEWORK/include/python3.13"
    -Wno-deprecated-declarations
)

# A: the delegate header set. scene_rdl2's own interface definitions included.
"${CXX:-clang++}" "${common[@]}" \
    -DPROBE_WITH_SCENE_RDL2 \
    -DPLATFORM_UNIX -DPLATFORM_APPLE -DTBB_SUPPRESS_DEPRECATED_MESSAGES \
    -D_LIBCPP_ENABLE_CXX17_REMOVED_UNARY_BINARY_FUNCTION \
    -isystem "$PXR_SHIM/include" \
    -isystem "$DEPS_ROOT/include" \
    -isystem "$INSTALL_DIR/include" \
    -isystem "$out/include" \
    "$src" -o "$out/probe-delegate" \
    -L"$MAYA_USD/lib" -lusd_tf -lusd_hd -lusd_usdImaging -lusd_usd -lusd_python \
    -L"$MAYA_PYTHON_FRAMEWORK/lib" -lpython3.13 \
    -L"$INSTALL_DIR/lib" -lscene_rdl2 \
    -Wl,-rpath,"$MAYA_USD/lib" -Wl,-rpath,"$INSTALL_DIR/lib" -Wl,-rpath,"$DEPS_ROOT/lib" \
    -Wl,-rpath,"$MAYA_PYTHON_FRAMEWORK/lib" \
    2> "$out/build-delegate.log" || { cat "$out/build-delegate.log" >&2; exit 1; }

# B: Maya's header set. No scene_rdl2, so only Maya's oneTBB is involved.
"${CXX:-clang++}" "${common[@]}" \
    -isystem "$USD_DEVKIT/include" \
    "$src" -o "$out/probe-maya" \
    -L"$MAYA_USD/lib" -lusd_tf -lusd_hd -lusd_usdImaging -lusd_usd -lusd_python \
    -L"$MAYA_PYTHON_FRAMEWORK/lib" -lpython3.13 \
    -Wl,-rpath,"$MAYA_USD/lib" -Wl,-rpath,"$MAYA_PYTHON_FRAMEWORK/lib" \
    2> "$out/build-maya.log" || { cat "$out/build-maya.log" >&2; exit 1; }

# Maya's libpython has the install name
# @executable_path/../Frameworks/Python.framework/..., which only resolves
# inside Maya.app. Point dyld at Maya's Frameworks dir for these probes.
export DYLD_FRAMEWORK_PATH="$MAYA_LOCATION/Maya.app/Contents/Frameworks"
"$out/probe-delegate" > "$out/delegate.txt"
"$out/probe-maya"     > "$out/maya.txt"

echo "== TBB 2020.3 (delegate) vs oneTBB 2022 (Maya) =="
# Lines pair up because both builds print the same probes in the same order;
# only the delegate build appends the "run" line.
paste -d'|' "$out/delegate.txt" "$out/maya.txt" | awk -F'|' '
    $2 == ""  { print "       " $1; next }
    $1 == $2  { print "same   " $1; next }
              { print "DIFF   " $1; print "       " $2 }'

must_diff=$(diff <(grep '^must' "$out/delegate.txt") <(grep '^must' "$out/maya.txt") || true)
if [ -z "$must_diff" ]; then
    echo "RESULT: PASS - every layout the delegate depends on is identical."
else
    echo "RESULT: FAIL - a \"must\" layout differs; see PLAN.md §7 Risk 1."
    exit 2
fi
