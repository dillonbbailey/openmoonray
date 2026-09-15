#!/usr/bin/env bash
# USD 25.08 overlay; the established local dependency prefix is read-only.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
export MOONRAY_USD25_ROOT="${MOONRAY_USD25_ROOT:-$MOONRAY_LOCAL_ROOT/usd25}"
export USD25_ROOT="$MOONRAY_USD25_ROOT/usd"
mkdir -p "$MOONRAY_USD25_ROOT"/{sources,build,logs,validation}
export PATH="$USD25_ROOT/bin:$PATH"
export LD_LIBRARY_PATH="$USD25_ROOT/lib:$DEPS_ROOT/lib"
export PYTHONPATH="$USD25_ROOT/lib/python:$DEPS_ROOT/lib/python3.10/site-packages"
export CMAKE_PREFIX_PATH="$USD25_ROOT:$DEPS_ROOT"
export PXR_USD_LOCATION="$USD25_ROOT" PXR_INCLUDE_DIRS="$USD25_ROOT/include"
export PXR_PLUGINPATH_NAME="$USD25_ROOT/lib/usd"
