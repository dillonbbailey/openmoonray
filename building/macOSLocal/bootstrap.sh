#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Creates the workspace-local Python environment that usdview needs.
# Installs nothing outside $MOONRAY_LOCAL_ROOT.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

for tool in "$MOONRAY_PYTHON_BASE" cmake git; do
  command -v "$tool" >/dev/null 2>&1 || [[ -x "$tool" ]] || {
    echo "Required tool not found: $tool" >&2; exit 1; }
done
[[ -d "$MOONRAY_PYTHON_FRAMEWORK" ]] || {
  echo "Python 3.9 framework not found at $MOONRAY_PYTHON_FRAMEWORK" >&2
  echo "Install the Xcode Command Line Tools, or set MOONRAY_PYTHON_FRAMEWORK." >&2
  exit 1; }

# MoonRay's XPU mode compiles a .metal shader, which needs the Metal Toolchain.
# That toolchain is an Xcode component and cannot live in the workspace, so this
# is a warning rather than an error: build with -DMOONRAY_USE_METAL=NO to skip
# XPU entirely, or install it with the command below.
if ! /usr/bin/xcrun metal --version >/dev/null 2>&1; then
  cat >&2 <<'WARN'

WARNING: the Metal Toolchain is not installed, so MoonRay's XPU mode cannot be
compiled. Either install it:

    xcodebuild -downloadComponent MetalToolchain

or build without XPU:

    bash building/macOSLocal/build-moonray.sh -DMOONRAY_USE_METAL=NO

WARN
fi

if [[ ! -x "$VENV_ROOT/bin/python3" ]]; then
  echo "Creating venv at $VENV_ROOT from $MOONRAY_PYTHON_BASE"
  "$MOONRAY_PYTHON_BASE" -m venv "$VENV_ROOT"
fi

# PySide6 6.3.1 and PyOpenGL are the Qt-for-Python and GL bindings USD 22.11 was
# released against (USD VERSIONS.md). 6.3.1 ships a cp36-abi3 universal2 wheel,
# so it loads on the Command Line Tools Python 3.9 on Apple Silicon.
# PyOpenGL is pinned to 3.1.7 rather than USD's tested 3.1.5: 3.1.5 cannot
# locate the OpenGL framework through the dyld shared cache on macOS 11+.
"$VENV_ROOT/bin/python3" -m pip install --upgrade pip
# numpy is needed by USD's Vt array bindings and by the OpenImageIO Python
# module. Pinned below 2.0: USD 22.11 and OIIO 2.3 predate the numpy 2 ABI.
"$VENV_ROOT/bin/python3" -m pip install "PySide6==6.3.1" "PyOpenGL==3.1.7" "numpy==1.26.4"

"$VENV_ROOT/bin/python3" - <<'PY'
import PySide6, OpenGL, numpy
from PySide6 import QtCore, QtWidgets, QtOpenGL   # noqa: F401
import OpenGL.GL                                   # noqa: F401
print(f"PySide6 {PySide6.__version__} (Qt {QtCore.qVersion()}), "
      f"PyOpenGL {OpenGL.__version__}, numpy {numpy.__version__}")
PY
[[ -x "$VENV_ROOT/bin/pyside6-uic" ]] || { echo "pyside6-uic missing from venv" >&2; exit 1; }

echo
echo "Bootstrap complete."
echo "  workspace : $MOONRAY_LOCAL_ROOT"
echo "  venv      : $VENV_ROOT"
echo "Next: bash building/macOSLocal/build-deps.sh"
