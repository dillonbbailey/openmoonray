#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Generate the Maya 2027 module for the delegate installed by
# build-delegate.sh (PLAN.md §6, milestone M2):
#
#   $MAYA_LOCAL_ROOT/module/
#   ├── moonray.mod
#   └── moonray/2027/usd/
#       ├── mayaUsdPlugInfo.json         PlugPath bundle/2511, VersionCheck USD 0.25.11
#       └── bundle/2511/
#           ├── plugInfo.json            {"Includes": ["*/"]}
#           └── <plugin>/plugInfo.json   LibraryPath made absolute
#
# Nothing is written to /Users/Shared/Autodesk/modules; run-maya.sh points
# MAYA_MODULE_PATH here. Every path is absolute: libmayaUsd rejects relative
# MAYA_PXR_PLUGINPATH_NAME entries, and USD resolves a relative LibraryPath
# lexically, so a symlinked plugInfo.json would point at the wrong dylib.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

install="$MAYA_LOCAL_ROOT/install"
module="$MAYA_LOCAL_ROOT/module"
root="$module/moonray/2027"
bundle="$root/usd/bundle/2511"
[ -d "$install/plugin/pxr" ] || { echo "run build-delegate.sh first" >&2; exit 1; }

rm -rf "$module"
mkdir -p "$bundle"

cat > "$root/usd/mayaUsdPlugInfo.json" <<'EOF'
{
    "MayaUsdIncludes": [
        {
            "PlugPath": "bundle/2511",
            "VersionCheck": { "Python": "3", "USD": "0.25.11" }
        }
    ]
}
EOF
echo '{ "Includes": [ "*/" ] }' > "$bundle/plugInfo.json"

for info in "$install"/plugin/pxr/*/plugInfo.json; do
    name="$(basename "$(dirname "$info")")"
    mkdir -p "$bundle/$name"
    # "LibraryPath": "../../<name>.dylib" -> absolute path in $install/plugin
    sed -E "s|\"LibraryPath\": *\"\.\./\.\./([^\"]+)\"|\"LibraryPath\": \"$install/plugin/\\1\"|" \
        "$info" > "$bundle/$name/plugInfo.json"
    lib="$(sed -nE 's|.*"LibraryPath": *"([^"]+)".*|\1|p' "$bundle/$name/plugInfo.json")"
    [ -f "$lib" ] || { echo "error: $name LibraryPath '$lib' does not exist" >&2; exit 1; }
done

# Runtime environment for the delegate and the Arras execComp child, which
# inherits Maya's environment and is found on PATH.
cat > "$module/moonray.mod" <<EOF
+ MAYAVERSION:2027 PLATFORM:mac moonray 1.0 $root
MAYA_PXR_PLUGINPATH_NAME += $root/usd
MOONRAY_CLASS_PATH = $INSTALL_DIR/shader_json
RDL2_DSO_PATH = $INSTALL_DIR/rdl2dso
ARRAS_SESSION_PATH = $INSTALL_DIR/sessions
REZ_MOONRAY_ROOT = $INSTALL_DIR
PATH +:= $INSTALL_DIR/bin
EOF

echo "Maya module ready: $module"
find "$module" -type f | sed "s|^$module/|  |" | sort
