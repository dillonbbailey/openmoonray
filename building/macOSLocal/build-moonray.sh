#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Configures and builds MoonRay against the workspace-local dependencies.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

[[ -f "$DEPS_ROOT/include/pxr/pxr.h" ]] || {
  echo "USD not found in $DEPS_ROOT. Run build-deps.sh first." >&2; exit 1; }

cd "$MOONRAY_SOURCE_ROOT"
cmake --preset macos-local-release "$@"
cmake --build --preset macos-local-release

# The Hydra render delegate reads shader descriptions from $INSTALL_DIR/shader_json.
# RDL2_DSO_PATH must be set: without it rdl2_json_exporter only describes the
# built-in shaders, the Sdr registry never learns about DwaBaseMaterial and
# friends, and every USD material silently resolves to no material at all --
# which renders as a fully transparent image rather than an error.
if [[ ! -d "$INSTALL_DIR/shader_json" ]]; then
  echo "Generating shader descriptions..."
  RDL2_DSO_PATH="$INSTALL_DIR/rdl2dso" \
    "$INSTALL_DIR/bin/rdl2_json_exporter" --out "$INSTALL_DIR/shader_json/" --sparse
  echo "  $(ls "$INSTALL_DIR/shader_json" | wc -l | tr -d ' ') shader descriptions written"
fi

echo "MoonRay installed into $INSTALL_DIR"
echo "Next: source building/macOSLocal/setup.sh"
