#!/usr/bin/env bash
# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Targeted rebuild: add libpng to $DEPS_ROOT and rebuild OpenImageIO 2.3.20
# with PNG support, without a full build-deps.sh. The first deps build had no
# libpng, so OIIO left out its PNG plugin: MoonRay could not read PNG
# textures and oiiotool / the OIIO Python module could not write PNGs (the
# Maya Material Editor's previews). third-party/CMakeLists.txt now builds
# libpng before OIIO, so full builds get the same result.
#
# Same OIIO commit and CMake arguments as third-party/CMakeLists.txt; same
# version and ABI, so MoonRay itself needs no rebuild. The previous OIIO
# libraries are kept in $DEPS_ROOT/backup-oiio-nopng.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

work="$MOONRAY_LOCAL_ROOT/build/oiio-png"
mkdir -p "$work"
jobs="-j$(sysctl -n hw.ncpu)"
common=(-DCMAKE_POLICY_VERSION_MINIMUM=3.5
        -DCMAKE_PREFIX_PATH="$DEPS_ROOT"
        -DCMAKE_IGNORE_PATH=/opt/homebrew -DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew
        -DCMAKE_INSTALL_PREFIX="$DEPS_ROOT" -DCMAKE_BUILD_TYPE=Release)

# libpng (zlib from the macOS SDK).
[ -d "$work/libpng" ] || git clone --depth 1 --branch v1.6.44 https://github.com/pnggroup/libpng "$work/libpng"
cmake -S "$work/libpng" -B "$work/libpng-build" "${common[@]}" \
    -DPNG_SHARED=ON -DPNG_STATIC=OFF -DPNG_TESTS=OFF -DPNG_TOOLS=OFF -DPNG_FRAMEWORK=OFF
cmake --build "$work/libpng-build" -- "$jobs"
cmake --install "$work/libpng-build"

# OpenImageIO, as in third-party/CMakeLists.txt.
if [ ! -d "$work/oiio" ]; then
    git clone https://github.com/OpenImageIO/oiio "$work/oiio"
    git -C "$work/oiio" checkout --quiet 331a323468928c8017ad048b26d47c4e57a724a7 # 2.3.20.0
fi
cmake -S "$work/oiio" -B "$work/oiio-build" "${common[@]}" \
    -DBoost_ROOT="$DEPS_ROOT" -DIMath_ROOT="$DEPS_ROOT" \
    -DDISABLE_CMAKE_SEARCH_PATHS=TRUE -DCMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH=TRUE \
    -DOpenEXR_ROOT="$DEPS_ROOT" -DPNG_ROOT="$DEPS_ROOT" \
    -DUSE_QT=0 -DUSE_PYTHON=1 -DPython_EXECUTABLE="$MOONRAY_PYTHON" | tee "$work/oiio-configure.log" | grep -i "png" || true
grep -qi "PNG library not found\|Could NOT find PNG" "$work/oiio-configure.log" && { echo "error: OIIO did not find libpng" >&2; exit 1; }
cmake --build "$work/oiio-build" -- "$jobs"

backup="$DEPS_ROOT/backup-oiio-nopng"
if [ ! -d "$backup" ]; then
    mkdir -p "$backup/lib" "$backup/bin"
    cp -a "$DEPS_ROOT"/lib/libOpenImageIO* "$backup/lib/"
    cp -a "$DEPS_ROOT/lib/python3.9/site-packages/OpenImageIO" "$backup/"
    cp -a "$DEPS_ROOT/bin/oiiotool" "$backup/bin/"
fi
cmake --install "$work/oiio-build"
"$DEPS_ROOT/bin/oiiotool" --help | grep -A2 "^Input formats\|format_list" | head -3 || true
echo "OpenImageIO formats: $(DYLD_LIBRARY_PATH="$DEPS_ROOT/lib" "$MOONRAY_PYTHON" -c 'import sys; sys.path.insert(0, "'"$DEPS_ROOT"'/lib/python3.9/site-packages"); import OpenImageIO as o; print(o.get_string_attribute("format_list"))')"
