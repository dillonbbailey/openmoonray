#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env-usd25.sh"
usd_source="$MOONRAY_USD25_ROOT/sources/OpenUSD-25.08"
if [[ ! -d "$usd_source" ]]; then
  curl -fL --retry 3 https://codeload.github.com/PixarAnimationStudios/OpenUSD/tar.gz/refs/tags/v25.08 \
    -o "$MOONRAY_USD25_ROOT/sources/OpenUSD-v25.08.tar.gz"
  tar -xzf "$MOONRAY_USD25_ROOT/sources/OpenUSD-v25.08.tar.gz" -C "$MOONRAY_USD25_ROOT/sources"
fi
# USD's fallback TBB import must allow a consumer that already found TBB.
if ! grep -q 'elseif (NOT TARGET TBB::tbb)' "$usd_source/pxr/pxrConfig.cmake.in"; then
  patch -d "$usd_source" -p1 < "$MOONRAY_LOCAL_SCRIPTS/patches/usd25-tbb-config.patch"
fi
cmake -S "$usd_source" -B "$MOONRAY_USD25_ROOT/build/usd" -G Ninja \
  -DCMAKE_INSTALL_PREFIX="$USD25_ROOT" -DCMAKE_PREFIX_PATH="$DEPS_ROOT" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_INSTALL_RPATH="$USD25_ROOT/lib;$DEPS_ROOT/lib" -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON \
  -DPython3_EXECUTABLE="$DEPS_ROOT/bin/python" -DPXR_PY_UNDEFINED_DYNAMIC_LOOKUP=OFF \
  -DPXR_BUILD_TESTS=OFF -DPXR_BUILD_EXAMPLES=OFF -DPXR_BUILD_TUTORIALS=OFF \
  -DPXR_BUILD_USD_TOOLS=ON -DPXR_BUILD_USDVIEW=ON \
  -DPXR_ENABLE_PTEX_SUPPORT=OFF -DPXR_ENABLE_OPENVDB_SUPPORT=OFF \
  -DPXR_ENABLE_VULKAN_SUPPORT=OFF -DTBB_ROOT="$DEPS_ROOT" -DTBB_USE_DEBUG_BUILD=OFF \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF
cmake --build "$MOONRAY_USD25_ROOT/build/usd" --parallel "${BUILD_JOBS:-18}"
cmake --install "$MOONRAY_USD25_ROOT/build/usd"
