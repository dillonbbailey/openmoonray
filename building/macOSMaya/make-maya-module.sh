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
    # Resource files next to plugInfo.json (e.g. a schema's generatedSchema.usda).
    find "$(dirname "$info")" -maxdepth 1 -type f ! -name plugInfo.json -exec cp {} "$bundle/$name/" \;
    # "LibraryPath": "../../<name>.dylib" -> absolute path in $install/plugin
    sed -E "s|\"LibraryPath\": *\"\.\./\.\./([^\"]+)\"|\"LibraryPath\": \"$install/plugin/\\1\"|" \
        "$info" > "$bundle/$name/plugInfo.json"
    lib="$(sed -nE 's|.*"LibraryPath": *"([^"]+)".*|\1|p' "$bundle/$name/plugInfo.json")"
    [ -z "$lib" ] || [ -f "$lib" ] || { echo "error: $name LibraryPath '$lib' does not exist" >&2; exit 1; }
done

# Arras session definitions. With "current-environment" packaging, execComp
# inherits Maya's whole environment; a computation's "environment" block takes
# precedence over it. Clear what points into Maya's Python 3.13 / USD 25.11 so
# the render side (built against USD 22.11 / Python 3.9) never picks it up.
# A fixed workingDirectory: Maya launched from the Dock runs with cwd "/".
# Based on this build's own sessiondefs, which also carry "enableDepthBuffer"
# (without it depth arrives as zeros and Maya's grid draws over the image).
sessions="$root/sessions"
workdir="$MAYA_LOCAL_ROOT/arras-work"
mkdir -p "$sessions" "$workdir"
for def in hd_single hd_multi; do
    "$MOONRAY_PYTHON_BASE" - "$install/sessions/$def.sessiondef" "$sessions/$def.sessiondef" "$workdir" <<'EOF'
import json, sys
src, dst, workdir = sys.argv[1:4]
d = json.load(open(src))
clear = ["PYTHONPATH", "PYTHONHOME", "PXR_PLUGINPATH_NAME",
         "PXR_MTLX_PLUGIN_SEARCH_PATHS", "PXR_MTLX_STDLIB_SEARCH_PATHS",
         "MATERIALX_SEARCH_PATH", "USD_LOCATION"]
for name, comp in d["computations"].items():
    if name.startswith("("):
        continue
    comp.setdefault("environment", {}).update({k: "" for k in clear})
    comp["workingDirectory"] = workdir
json.dump(d, open(dst, "w"), indent=4)
EOF
done

# Python for Maya, used from the source tree: the MoonRay menu (userSetup.py)
# and the Material Editor (maya/material_editor).
maya_src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/maya"

# Runtime environment for the delegate and the Arras execComp child, which
# is found on PATH. Use "+=" with absolute values: "+:=" resolves the value
# relative to the module root ("<root>//abs/path"), which broke the PATH entry.
cat > "$module/moonray.mod" <<EOF
+ MAYAVERSION:2027 PLATFORM:mac moonray 1.0 $root
MAYA_PXR_PLUGINPATH_NAME += $root/usd
MOONRAY_CLASS_PATH = $INSTALL_DIR/shader_json
RDL2_DSO_PATH = $INSTALL_DIR/rdl2dso
ARRAS_SESSION_PATH = $sessions
REZ_MOONRAY_ROOT = $INSTALL_DIR
PATH += $INSTALL_DIR/bin
PYTHONPATH += $maya_src/scripts
PYTHONPATH += $maya_src/material_editor
EOF

echo "Maya module ready: $module"
find "$module" -type f | sed "s|^$module/|  |" | sort
