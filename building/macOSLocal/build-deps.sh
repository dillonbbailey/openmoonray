#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Downloads, builds and installs every third-party dependency into $DEPS_ROOT.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

[[ -x "$VENV_ROOT/bin/pyside6-uic" ]] || {
  echo "Run building/macOSLocal/bootstrap.sh first." >&2; exit 1; }

cmake -S "$MOONRAY_LOCAL_SCRIPTS/third-party" -B "$DEPS_BUILD_DIR" \
  -DInstallRoot="$DEPS_ROOT" \
  -DPythonExecutable="$MOONRAY_PYTHON" \
  -DPythonFramework="$MOONRAY_PYTHON_FRAMEWORK" \
  -DPySideBinDir="$VENV_ROOT/bin" \
  "$@"
cmake --build "$DEPS_BUILD_DIR"
echo "Dependencies installed into $DEPS_ROOT"
