#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Configure, build and install only the Hydra delegate against Maya 2027's
# USD 25.11 (milestone M1). Output: $MAYA_LOCAL_ROOT/install. Extra
# arguments are passed to the configure step.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

[ -f "$PXR_SHIM/pxrConfig.cmake" ] || bash "$MOONRAY_MAYA_SCRIPTS/unpack-usd-devkit.sh"

build="$MAYA_LOCAL_ROOT/build"
install="$MAYA_LOCAL_ROOT/install"

cmake -S "$MOONRAY_MAYA_SCRIPTS/hydra-only" -B "$build" \
    -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$install" \
    -DCMAKE_PREFIX_PATH="$INSTALL_DIR;$DEPS_ROOT" \
    -DCMAKE_IGNORE_PATH=/opt/homebrew \
    -DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_CXX_FLAGS=-D_LIBCPP_ENABLE_CXX17_REMOVED_UNARY_BINARY_FUNCTION \
    -Dpxr_DIR="$PXR_SHIM" \
    -DMAYA_LOCATION="$MAYA_LOCATION" \
    -DPXR_USD_LOCATION="$PXR_SHIM" \
    -DPython_EXECUTABLE="$MAYAPY" \
    -DPython3_EXECUTABLE="$MAYAPY" \
    -DBOOST_PYTHON_COMPONENT_NAME=python39 \
    "$@"
cmake --build "$build" --parallel "$BUILD_JOBS"
# Install fresh: PinMayaUsd.cmake rewrites installed binaries in place, which
# CMake's own RPATH fixup on a reinstall would then trip over.
rm -rf "$install"
cmake --install "$build"

bash "$MOONRAY_MAYA_SCRIPTS/check-delegate-linkage.sh"
