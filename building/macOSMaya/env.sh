# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Source this file. Layers the Maya 2027 paths on top of the workspace-local
# recipe in ../macOSLocal. Everything this recipe writes lives under
# $MAYA_LOCAL_ROOT; nothing inside /Applications is modified.

source "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/../macOSLocal/env.sh"
export MOONRAY_MAYA_SCRIPTS="$MOONRAY_SOURCE_ROOT/building/macOSMaya"

# --- Maya 2027 install (read only) -------------------------------------------
export MAYA_LOCATION="${MAYA_LOCATION:-/Applications/Autodesk/maya2027}"
if [ -z "${MAYAUSD:-}" ]; then
    # Newest MayaUSD build installed for Maya 2027.
    MAYAUSD="$(ls -d /Applications/Autodesk/mayausd/maya2027/*/mayausd 2>/dev/null | sort | tail -n 1)"
fi
export MAYAUSD
export MAYA_USD="$MAYAUSD/USD"             # pxrConfig.cmake, lib/libusd_*.dylib, devkit.tgz
export MAYA_PYTHON_FRAMEWORK="$MAYA_LOCATION/Maya.app/Contents/Frameworks/Python.framework/Versions/Current"
export MAYAPY="$MAYA_LOCATION/Maya.app/Contents/bin/mayapy"

# --- workspace layout ---------------------------------------------------------
export MAYA_LOCAL_ROOT="$MOONRAY_LOCAL_ROOT/maya"
export USD_DEVKIT="$MAYA_LOCAL_ROOT/usd-devkit"   # devkit.tgz extracted verbatim
export PXR_SHIM="$MAYA_LOCAL_ROOT/pxr"            # pxr_DIR exposing only pxr/ headers
mkdir -p "$MAYA_LOCAL_ROOT"/{logs,validation}

for _p in "$MAYA_LOCATION" "$MAYA_USD/devkit.tgz" "$MAYA_USD/lib"; do
    [ -e "$_p" ] || echo "macOSMaya/env.sh: warning: missing $_p" >&2
done
unset _p
