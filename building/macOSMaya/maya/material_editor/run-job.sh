#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Run a Material Editor background job (preview render, export, texture
# thumbnail, .tx conversion, bake) outside Maya:
#   run-job.sh <worker|render_view_worker> <args...>
# The editor starts this with an empty environment (env -i), so nothing from
# Maya's Python 3.13 / USD 25.11 leaks in. Jobs use the workspace's MoonRay
# build (building/macOSLocal: USD 22.11, Python 3.9 with OpenImageIO), as
# MoonLab's run-worker.sh / run-render-view.sh use its MoonRay environment.
set -eo pipefail
editor_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$editor_dir/../../../macOSLocal/setup.sh" >/dev/null
export PYTHONPATH="$editor_dir:$PYTHONPATH"
export PXR_PLUGINPATH_NAME="$editor_dir/assets/usd_schemas${PXR_PLUGINPATH_NAME:+:$PXR_PLUGINPATH_NAME}"
module="$1"; shift
# A new session, so cancelling can signal the job's whole process group
# (MoonLab uses setsid(1), which macOS does not have).
exec python -c 'import os, runpy, sys; os.setsid(); sys.argv[0] = sys.argv[1]; del sys.argv[1]; runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)' \
    "moonray_material_editor.$module" "$@"
