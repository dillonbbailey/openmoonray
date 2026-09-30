#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Extract MayaUSD's devkit.tgz into the workspace and assemble $PXR_SHIM, a
# pxr_DIR that resolves Maya's own USD 25.11 targets but exposes only the pxr/
# headers. The devkit also bundles boost, oneTBB, OpenEXR, Imath and OIIO
# headers; keeping those off the include path stops them shadowing the deps
# versions the prebuilt MoonRay core was compiled against. See PLAN.md §3-4.
#
# Idempotent. Pass --force to re-extract.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

if [ "${1:-}" = "--force" ]; then
    rm -rf "$USD_DEVKIT" "$PXR_SHIM"
fi

# --- 1. extract ---------------------------------------------------------------
stamp="$USD_DEVKIT/.extracted-from"
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$MAYA_USD/devkit.tgz" ]; then
    echo "Extracting $MAYA_USD/devkit.tgz -> $USD_DEVKIT"
    rm -rf "$USD_DEVKIT"
    mkdir -p "$USD_DEVKIT"
    tar -xzf "$MAYA_USD/devkit.tgz" -C "$USD_DEVKIT"
    echo "$MAYA_USD/devkit.tgz" > "$stamp"
fi
for f in cmake/pxrTargets.cmake cmake/pxrTargets-relwithdebinfo.cmake include/pxr/pxr.h; do
    [ -f "$USD_DEVKIT/$f" ] || { echo "error: devkit is missing $f" >&2; exit 1; }
done

# --- 2. assemble the shim -------------------------------------------------------
# pxrTargets.cmake derives _IMPORT_PREFIX from the parent of cmake/, so cmake/,
# include/ and lib/ must all be siblings of the generated pxrConfig.cmake.
mkdir -p "$PXR_SHIM/include"
ln -sfn "$USD_DEVKIT/cmake"       "$PXR_SHIM/cmake"
ln -sfn "$USD_DEVKIT/include/pxr" "$PXR_SHIM/include/pxr"
ln -sfn "$MAYA_USD/lib"           "$PXR_SHIM/lib"
sed -e "s|@MAYA_LOCATION@|$MAYA_LOCATION|g" \
    "$MOONRAY_MAYA_SCRIPTS/pxr-maya/pxrConfig.cmake.in" > "$PXR_SHIM/pxrConfig.cmake"

version="$(sed -n 's/^#define PXR_VERSION \([0-9]*\)$/\1/p' "$PXR_SHIM/include/pxr/pxr.h")"
echo "pxr shim ready: $PXR_SHIM (PXR_VERSION $version)"
echo "  include/ exposes: $(ls "$PXR_SHIM/include")"
