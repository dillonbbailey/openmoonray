#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Build the debug-only closehook.dylib into $MAYA_LOCAL_ROOT/debug. Use with
#   MAYA_DYLD_INSERT_LIBRARIES=$MAYA_LOCAL_ROOT/debug/closehook.dylib \
#       bash building/macOSMaya/m3-render-test.sh
# It logs who closes the Arras client socket and prints a backtrace on
# SIGSEGV/SIGBUS that Arnold's or Maya's handlers would otherwise swallow.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
out="$MAYA_LOCAL_ROOT/debug"
mkdir -p "$out"
clang -arch arm64 -dynamiclib -O1 -g -o "$out/closehook.dylib" "$MOONRAY_MAYA_SCRIPTS/debug/closehook.c"
codesign -f -s - "$out/closehook.dylib"
echo "$out/closehook.dylib"
