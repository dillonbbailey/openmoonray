#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Render USD files with Moonray through Maya's usdrecord twice - on USD's
# legacy imaging path (UsdImagingDelegate) and on the default scene-index path
# that Maya's Hydra viewport uses - and compare what reaches MoonRay:
# synced Hydra prims, RDL scene objects, and the images.
#   compare-imaging.sh <outdir> <file.usd>... [-- extra usdrecord args]
set -euo pipefail
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$1"; shift
files=(); extra=()
while [ $# -gt 0 ]; do
    if [ "$1" = "--" ]; then shift; extra=("$@"); break; fi
    files+=("$1"); shift
done
USDIMAGINGGL_ENGINE_ENABLE_SCENE_INDEX=0 bash "$scripts/usdrecord-test.sh" "$out/legacy" "${files[@]}" -- "${extra[@]}" >/dev/null
names=($(USDIMAGINGGL_ENGINE_ENABLE_SCENE_INDEX=1 bash "$scripts/usdrecord-test.sh" "$out/sceneindex" "${files[@]}" -- "${extra[@]}" | sed 's/:.*//'))

source "$scripts/env.sh" >/dev/null
"$MAYAPY" - "$out" "${names[@]}" <<'EOF' 2>&1 | grep -v "^qt\."
import collections, os, re, sys
from PySide6.QtGui import QImage
out, names = sys.argv[1], sys.argv[2:]

def synced(path):
    if not os.path.exists(path): return collections.Counter()
    return collections.Counter(re.findall(r"^SyncStart (\w+)", open(path).read(), re.M))

def rdl(path):
    if not os.path.exists(path): return collections.Counter()
    c = collections.Counter(re.findall(r'^([A-Za-z]\w*)\(', open(path).read(), re.M))
    for k in ("RenderOutput", "UserData", "PerspectiveCamera", "Layer", "GeometrySet", "LightSet"):
        c.pop(k, None)
    return c

def pixels(path):
    i = QImage(path)
    if i.isNull(): return None
    i = i.convertToFormat(QImage.Format_ARGB32)
    return [(i.pixel(x, y) >> 16 & 255, i.pixel(x, y) >> 8 & 255, i.pixel(x, y) & 255, i.pixel(x, y) >> 24 & 255)
            for y in range(0, i.height(), 2) for x in range(0, i.width(), 2)]

def fmt(c): return ", ".join(f"{k} {v}" for k, v in sorted(c.items())) or "-"

for n in names:
    a, b = synced(f"{out}/legacy/{n}.hdm.log"), synced(f"{out}/sceneindex/{n}.hdm.log")
    ra, rb = rdl(f"{out}/legacy/{n}.rdla"), rdl(f"{out}/sceneindex/{n}.rdla")
    pa, pb = pixels(f"{out}/legacy/{n}.png"), pixels(f"{out}/sceneindex/{n}.png")
    if pa is None or pb is None or len(pa) != len(pb):
        img = "image missing/size differs"
    else:
        diff = sum(1 for p, q in zip(pa, pb) if max(abs(p[i] - q[i]) for i in range(4)) > 24) / len(pa)
        cov = sum(1 for p in pb if p[3] > 0) / len(pb)
        img = f"pixels differing {100 * diff:5.1f}%  (scene-index coverage {100 * cov:4.1f}%)"
    verdict = "SAME" if (a == b and ra == rb) else "DIFF"
    print(f"== {n}: {verdict}  {img}")
    if a != b: print(f"   synced legacy: {fmt(a)}\n   synced s-idx : {fmt(b)}")
    if ra != rb: print(f"   rdl legacy   : {fmt(ra)}\n   rdl s-idx    : {fmt(rb)}")
    if a == b and ra == rb: print(f"   rdl: {fmt(rb)}")
EOF
