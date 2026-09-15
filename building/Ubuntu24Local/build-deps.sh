#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cmake -S "$MOONRAY_LOCAL_SCRIPTS/third-party" -B "$MOONRAY_LOCAL_ROOT/build/dependencies" \
  -G 'Unix Makefiles' -DInstallRoot="$DEPS_ROOT"
cmake --build "$MOONRAY_LOCAL_ROOT/build/dependencies" --parallel "${BUILD_JOBS:-16}" "$@"
