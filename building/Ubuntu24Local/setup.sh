#!/usr/bin/env bash
# Source this file to run the local installation, including usdview.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
export PATH="$MOONRAY_LOCAL_ROOT/install/bin:$PATH"
export PYTHONPATH="$MOONRAY_LOCAL_ROOT/install/python/lib/python3.10:$PYTHONPATH"
export LD_LIBRARY_PATH="$MOONRAY_LOCAL_ROOT/install/lib:$DEPS_ROOT/lib:$DEPS_ROOT/targets/x86_64-linux/lib"
export RDL2_DSO_PATH="$MOONRAY_LOCAL_ROOT/install/rdl2dso"
export REZ_MOONRAY_ROOT="$MOONRAY_LOCAL_ROOT/install"
export ARRAS_SESSION_PATH="$MOONRAY_LOCAL_ROOT/install/sessions"
export MOONRAY_CLASS_PATH="$MOONRAY_LOCAL_ROOT/install/shader_json"
export PXR_PLUGINPATH_NAME="$MOONRAY_LOCAL_ROOT/install/plugin/pxr:$DEPS_ROOT/lib/usd"
