# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Source this file. Every tool, library, cache and build tree used by this
# recipe lives under $MOONRAY_LOCAL_ROOT. Nothing is written outside it except
# the source checkout itself. No system package manager is invoked and no
# shell profile is modified.

export MOONRAY_LOCAL_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export MOONRAY_SOURCE_ROOT="$(cd "$MOONRAY_LOCAL_SCRIPTS/../.." && pwd)"
export MOONRAY_LOCAL_ROOT="${MOONRAY_LOCAL_ROOT:-$MOONRAY_SOURCE_ROOT/../local-build}"
mkdir -p "$MOONRAY_LOCAL_ROOT"/{build,cache,config,downloads,logs,share,sources,tmp,validation}
export MOONRAY_LOCAL_ROOT="$(cd "$MOONRAY_LOCAL_ROOT" && pwd)"

# --- workspace layout -------------------------------------------------------
export DEPS_ROOT="$MOONRAY_LOCAL_ROOT/deps"        # all third-party installs
export VENV_ROOT="$MOONRAY_LOCAL_ROOT/venv"        # PySide6 / PyOpenGL for usdview
export INSTALL_DIR="$MOONRAY_LOCAL_ROOT/install"   # openmoonray install
export DEPS_BUILD_DIR="$MOONRAY_LOCAL_ROOT/build/deps"
export BUILD_DIR="$MOONRAY_LOCAL_ROOT/build/moonray"
mkdir -p "$DEPS_ROOT"/{bin,lib,include} "$INSTALL_DIR" "$DEPS_BUILD_DIR" "$BUILD_DIR"

# --- python -----------------------------------------------------------------
# The Command Line Tools Python 3.9 is the interpreter Boost.Python, OpenImageIO
# and USD are all compiled against (see building/macOS/user-config.jam). It is
# only *read* from; the venv below is where anything gets installed.
export MOONRAY_PYTHON_BASE="${MOONRAY_PYTHON_BASE:-/Library/Developer/CommandLineTools/usr/bin/python3}"
export MOONRAY_PYTHON_FRAMEWORK="${MOONRAY_PYTHON_FRAMEWORK:-/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9}"
export MOONRAY_PYTHON_VERSION=3.9
# Built against the base interpreter, but resolves imports from the venv, so
# USD's FindPySide/FindPyOpenGL see PySide6 and PyOpenGL without touching /usr.
export MOONRAY_PYTHON="$VENV_ROOT/bin/python3"

# --- keep every cache and temp file inside the workspace --------------------
export XDG_CACHE_HOME="$MOONRAY_LOCAL_ROOT/cache"
export XDG_CONFIG_HOME="$MOONRAY_LOCAL_ROOT/config"
export XDG_DATA_HOME="$MOONRAY_LOCAL_ROOT/share"
export PIP_CACHE_DIR="$MOONRAY_LOCAL_ROOT/cache/pip"
export TMPDIR="$MOONRAY_LOCAL_ROOT/tmp"
export PYTHONNOUSERSITE=1
export PIP_USER=0
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$PIP_CACHE_DIR" "$TMPDIR"

# --- build environment ------------------------------------------------------
# Homebrew is deliberately excluded from package discovery (the third-party
# CMakeLists also sets CMAKE_IGNORE_PATH) so a stray /opt/homebrew library can
# never be linked in. Homebrew's cmake binary itself is fine to use as a tool.
export CMAKE_PREFIX_PATH="$DEPS_ROOT"
export PKG_CONFIG_PATH="$DEPS_ROOT/lib/pkgconfig:$DEPS_ROOT/share/pkgconfig"
export ISPC="$DEPS_ROOT/bin/ispc"
export BUILD_JOBS="${BUILD_JOBS:-$(sysctl -n hw.ncpu)}"

# Package roots consumed by the moonray CMake finders.
export Boost_ROOT="$DEPS_ROOT"       TBB_ROOT="$DEPS_ROOT"        LUA_DIR="$DEPS_ROOT"
export Libuuid_ROOT="$DEPS_ROOT"     CppUnit_ROOT="$DEPS_ROOT"    JsonCpp_ROOT="$DEPS_ROOT"
export Libcurl_ROOT="$DEPS_ROOT"     Log4cplus_ROOT="$DEPS_ROOT"  OpenSubDiv_ROOT="$DEPS_ROOT"
export OpenVDB_ROOT="$DEPS_ROOT"     Random123_ROOT="$DEPS_ROOT"  ZLIB_ROOT="$DEPS_ROOT"
export PXR_USD_LOCATION="$DEPS_ROOT" PXR_INCLUDE_DIRS="$DEPS_ROOT/include"
export OIIO_PYTHON="$DEPS_ROOT/lib/python${MOONRAY_PYTHON_VERSION}/site-packages"
