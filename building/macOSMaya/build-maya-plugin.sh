#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Build the Maya plug-ins in maya/plugin against Maya 2027's own headers and
# libraries (no separate devkit download needed) into
# $MAYA_LOCAL_ROOT/plug-ins, which make-maya-module.sh puts on
# MAYA_PLUG_IN_PATH.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh" >/dev/null
maya="${MAYA_LOCATION:-/Applications/Autodesk/maya2027}"
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/maya/plugin"
out="$MAYA_LOCAL_ROOT/plug-ins"
mkdir -p "$out"
for cpp in "$src"/*.cpp; do
    name="$(basename "${cpp%.cpp}")"
    clang++ -std=c++17 -O2 -arch arm64 -mmacosx-version-min=14.0 \
        -DMAC_PLUGIN -DOSMac_ -D_BOOL -DREQUIRE_IOSTREAM -Wno-nontrivial-memcall \
        -I"$maya/include" -bundle -o "$out/$name.bundle" "$cpp" \
        -L"$maya/Maya.app/Contents/MacOS" -lOpenMaya -lOpenMayaRender -lFoundation \
        -Wl,-rpath,"$maya/Maya.app/Contents/MacOS"
    echo "built $out/$name.bundle"
done
