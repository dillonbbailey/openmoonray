#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Launch Maya 2027 with the workspace Moonray module (make-maya-module.sh).
# Arguments are passed to Maya; e.g. `run-maya.sh -prompt` for batch mode.
#
# Deliberately does not source env.sh: that sets build-only variables
# (PYTHONNOUSERSITE, TMPDIR, XDG_*, ...) that would change Maya's behaviour.
# Every runtime path is baked into moonray.mod instead.
set -euo pipefail
scripts="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
local_root="${MOONRAY_LOCAL_ROOT:-$scripts/../../../local-build}"
module="$(cd "$local_root" && pwd)/maya/module"
maya="${MAYA_LOCATION:-/Applications/Autodesk/maya2027}/Maya.app/Contents/MacOS/Maya"

[ -f "$module/moonray.mod" ] || { echo "run make-maya-module.sh first" >&2; exit 1; }
export MAYA_MODULE_PATH="$module${MAYA_MODULE_PATH:+:$MAYA_MODULE_PATH}"
exec "$maya" "$@"
