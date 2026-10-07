#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Render USD files with the Moonray delegate inside Maya's USD 25.11 / mayapy,
# headless, via Maya's usdrecord - the same delegate, USD and Arras path as
# Maya's Hydra viewport, but producing images. For each input writes
# <out>/<name>.png, .rdla (scene sent to MoonRay), .hdm.log and .log.
#   usdrecord-test.sh <outdir> <file.usd>... [-- extra usdrecord args]
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh" >/dev/null
out="$1"; shift
mkdir -p "$out"
files=(); extra=()
while [ $# -gt 0 ]; do
    if [ "$1" = "--" ]; then shift; extra=("$@"); break; fi
    files+=("$1"); shift
done
install="$MAYA_LOCAL_ROOT/install"
for f in "${files[@]}"; do
    name="$(basename "${f%.*}")"
    env -u PYTHONNOUSERSITE -u TMPDIR \
        PXR_PLUGINPATH_NAME="$install/plugin/pxr" PYTHONPATH="$MAYA_USD/lib/python" \
        MOONRAY_CLASS_PATH="$INSTALL_DIR/shader_json" RDL2_DSO_PATH="$INSTALL_DIR/rdl2dso" \
        ARRAS_SESSION_PATH="$MAYA_LOCAL_ROOT/module/moonray/2027/sessions" \
        PATH="$INSTALL_DIR/bin:$PATH" HDM_LOG_FILE="$out/$name.hdm.log" \
        HDMOONRAY_RDLA_OUTPUT="$out/$name.rdla" \
        "$MAYAPY" "$MAYA_USD/bin/usdrecord" --renderer Moonray "${extra[@]}" \
        "$f" "$out/$name.png" > "$out/$name.log" 2>&1 \
        && echo "$name: ok" || echo "$name: usdrecord FAILED (see $out/$name.log)"
done
