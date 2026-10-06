#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# M3 GUI test: launch Maya with the Moonray module, render a cube through
# Hydra Moonray, capture the viewport, quit. Output: $MAYA_LOCAL_ROOT/m3/<time>/.
# A watchdog kills Maya if it has not quit after $M3_TIMEOUT seconds.
set -euo pipefail
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
local_root="$(cd "${MOONRAY_LOCAL_ROOT:-$scripts/../../../local-build}" && pwd)"
export M3_OUT="$local_root/maya/m3/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$M3_OUT"

mel="$M3_OUT/startup.mel"
printf 'python("exec(open(\\"%s\\").read())");\n' "$scripts/m3-render-test.py" > "$mel"

# Isolated Maya preferences: test sessions quit with cmds.quit(), which saves
# prefs - including the plugin autoload list, emptied by -noAutoloadPlugins.
# Never touch the user's ~/Library/Preferences/Autodesk/maya.
export MAYA_APP_DIR="${M3_MAYA_APP_DIR:-$local_root/maya/app-dir}"
mkdir -p "$MAYA_APP_DIR"

# hdMoonray's call trace (connect, render, ...); HDMOONRAY_INFO does nothing.
export HDM_LOG_FILE="${HDM_LOG_FILE:-$M3_OUT/hdm.log}"
# M3_MAYA_ARGS: extra Maya flags, e.g. "-noAutoloadPlugins" (no MtoA etc.).
# shellcheck disable=SC2086
bash "$scripts/run-maya.sh" ${M3_MAYA_ARGS:-} -command "source \"$mel\"" > "$M3_OUT/maya.log" 2>&1 &
launcher=$!

timeout="${M3_TIMEOUT:-300}"
for _ in $(seq "$timeout"); do
    kill -0 "$launcher" 2>/dev/null || break
    sleep 1
done
if kill -0 "$launcher" 2>/dev/null; then
    echo "watchdog: Maya still running after ${timeout}s, killing it" | tee -a "$M3_OUT/m3.log" >&2
    pkill -9 -f "Maya.app/Contents/MacOS/Maya -command source \"$mel\"" || true
fi
wait "$launcher" 2>/dev/null || true
# execComp should not outlive Maya.
pgrep -fl execComp >> "$M3_OUT/orphans.txt" || true

echo "$M3_OUT"
