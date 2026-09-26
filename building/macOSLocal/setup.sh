# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Source this file to use the workspace-local MoonRay and USD installation.
#
# This replaces scripts/setup.sh for this recipe: that script requires a
# directory literally named "installs" and prepends system Python paths, both
# of which conflict with a self-contained workspace.

source "$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/env.sh"

export PATH="$INSTALL_DIR/bin:$DEPS_ROOT/bin:$VENV_ROOT/bin:$PATH"

# USD's Python modules, then OpenImageIO's, then MoonRay's. PySide6 and
# PyOpenGL are not listed: usdview runs under the venv interpreter, which finds
# them in its own site-packages.
export PYTHONPATH="$DEPS_ROOT/lib/python:$OIIO_PYTHON:$INSTALL_DIR/lib/python:${PYTHONPATH:-}"

# hd_render and the Hydra plugins link Python as @rpath/Python3.framework/...,
# but their RPATHs only cover the framework's lib/ directory, not the directory
# holding the framework itself. Name it explicitly so dyld can resolve it.
# $MOONRAY_PYTHON_FRAMEWORK is .../Frameworks/Python3.framework/Versions/3.9;
# dyld needs the directory that *contains* Python3.framework.
export DYLD_FRAMEWORK_PATH="$(cd "$MOONRAY_PYTHON_FRAMEWORK/../../.." && pwd):${DYLD_FRAMEWORK_PATH:-}"

# Prefer the workspace libraries without overriding a binary's own RPATH.
export DYLD_FALLBACK_LIBRARY_PATH="$INSTALL_DIR/lib:$DEPS_ROOT/lib:${DYLD_FALLBACK_LIBRARY_PATH:-/usr/local/lib:/usr/lib}"

export RDL2_DSO_PATH="$INSTALL_DIR/rdl2dso"       # MoonRay shader DSOs
export REZ_MOONRAY_ROOT="$INSTALL_DIR"            # XPU looks here for GPUShaders.ptx
export ARRAS_SESSION_PATH="$INSTALL_DIR/sessions" # Arras session definitions
export MOONRAY_CLASS_PATH="$INSTALL_DIR/shader_json"
export PXR_PLUGINPATH_NAME="$INSTALL_DIR/plugin/pxr:${PXR_PLUGINPATH_NAME:-}"

# usdview writes ~/.usdview by default; keep it out of the home directory.
export PXR_USDVIEW_SUPPRESS_STATE_SAVING=1

if [[ ! -d "$INSTALL_DIR/shader_json" && -x "$INSTALL_DIR/bin/rdl2_json_exporter" ]]; then
    echo "Building shader descriptions..."
    "$INSTALL_DIR/bin/rdl2_json_exporter" --out "$INSTALL_DIR/shader_json/" --sparse
fi

echo "MoonRay workspace ready: $MOONRAY_LOCAL_ROOT"
