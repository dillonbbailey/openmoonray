#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env-usd25.sh"
cmake -S "$MOONRAY_SOURCE_ROOT" -B "$MOONRAY_USD25_ROOT/build/moonray" \
  -G 'Unix Makefiles' -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PROJECT_openmoonray_INCLUDE="$MOONRAY_LOCAL_SCRIPTS/usd25-include-order.cmake" \
  -DCMAKE_INSTALL_PREFIX="$MOONRAY_USD25_ROOT/install" \
  -DCMAKE_PREFIX_PATH="$USD25_ROOT;$DEPS_ROOT" -Dpxr_DIR="$USD25_ROOT" -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF \
  -DPython_EXECUTABLE="$DEPS_ROOT/bin/python" \
  -DPYTHON_EXECUTABLE="$DEPS_ROOT/bin/python" \
  -DBOOST_PYTHON_COMPONENT_NAME=python310 \
  -DLibuuid_INCLUDE_DIRS="$DEPS_ROOT/include/uuid" \
  -DTBB_ROOT="$DEPS_ROOT" -DCMAKE_ISPC_COMPILER="$ISPC" \
  -DMOONRAY_USE_OPTIX="${MOONRAY_USE_OPTIX:-ON}" \
  -DCUDAToolkit_ROOT="$DEPS_ROOT" -DCMAKE_CUDA_COMPILER="$DEPS_ROOT/bin/nvcc" \
  -DCMAKE_CUDA_HOST_COMPILER="$CXX" -DOPTIX_ROOT="$DEPS_ROOT" \
  -DBUILD_QT_APPS=ON -DMOONRAY_BUILD_TESTING=OFF \
  -DSCENERDL2_BUILD_TESTING=OFF -DMCRTDENOISE_BUILD_TESTING=OFF \
  -DARRASCORE_BUILD_TESTING=OFF -DMOONSHINE_BUILD_TESTING=OFF \
  -DMOONSHINEUSD_BUILD_TESTING=OFF -DMCRTCOMPUTATION_BUILD_TESTING=OFF \
  -DMCRTDATAIO_BUILD_TESTING=OFF -DMCRTMESSAGES_BUILD_TESTING=OFF "$@"
cmake --build "$MOONRAY_USD25_ROOT/build/moonray" --parallel "${BUILD_JOBS:-16}"
cmake --install "$MOONRAY_USD25_ROOT/build/moonray"
# Upstream CMake omits the profile viewer's generated version module.
python - <<'PYVERSION'
import configparser
import os
from pathlib import Path
config = configparser.ConfigParser()
config.read(Path(os.environ['MOONRAY_SOURCE_ROOT']) / 'moonray/render_profile_viewer/setup.cfg')
target = Path(os.environ['MOONRAY_USD25_ROOT']) / 'install/python/render_profile_viewer/render_profile_viewer/_version.py'
target.write_text('__version__ = ' + repr(config['metadata']['version']) + '\n')
PYVERSION
source "$MOONRAY_LOCAL_SCRIPTS/setup-usd25.sh"
rdl2_json_exporter --out "$MOONRAY_CLASS_PATH/" --sparse
