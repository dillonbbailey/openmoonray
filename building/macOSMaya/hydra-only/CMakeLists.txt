# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Builds only the Hydra layer (hdMoonray + moonray_sdr_plugins) against
# Maya 2027's USD 25.11, reusing the MoonRay core already installed in
# $INSTALL_DIR. Driven by build-delegate.sh; see PLAN.md §4.
#
# hdMoonray skips its own find_package() calls for the MoonRay packages when
# it is not the top-level project, so they are found here instead.

cmake_minimum_required(VERSION 3.23.1)
project(moonray_maya_hydra LANGUAGES C CXX)

set(MOONRAY_SOURCE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." CACHE PATH "openmoonray checkout")
cmake_path(NORMAL_PATH MOONRAY_SOURCE_ROOT)
set(MOONRAY_INSTALL_DIR "$ENV{INSTALL_DIR}" CACHE PATH "Prebuilt MoonRay core install")
if(NOT EXISTS "${MOONRAY_INSTALL_DIR}/lib/cmake")
    message(FATAL_ERROR "MOONRAY_INSTALL_DIR='${MOONRAY_INSTALL_DIR}' has no lib/cmake; "
                        "build MoonRay with building/macOSLocal first")
endif()

# Local overrides first (FindLibatomic), then MoonRay's find modules.
list(APPEND CMAKE_MODULE_PATH
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake"
    "${MOONRAY_SOURCE_ROOT}/cmake_modules/cmake")
list(APPEND CMAKE_PREFIX_PATH "${MOONRAY_INSTALL_DIR}")
include(OMR_Platform)

# --- TBB: deps TBB 2020.3, bound before pxr is found ----------------------------
# The prebuilt MoonRay core was compiled against it. The shim's pxrConfig.cmake
# only creates TBB::tbb when it does not exist yet, so Maya's oneTBB is not
# linked here; its libusd_* still load their own copy at runtime. The layouts
# this relies on are checked by tbb-probe/tbb-probe.sh (PLAN.md §7 Risk 1).
find_package(TBB REQUIRED)

# --- USD: Maya's 25.11 through the shim ($PXR_SHIM) -----------------------------
find_package(pxr REQUIRED)
if(NOT PXR_VERSION EQUAL 2511)
    message(FATAL_ERROR "Expected Maya's USD 25.11, got PXR_VERSION ${PXR_VERSION} from ${pxr_DIR}")
endif()
message(STATUS "USD ${PXR_VERSION} from ${pxr_DIR}")

# The MoonRay core targets put $DEPS_ROOT/include on the include path, and it
# holds USD 22.11's pxr/ headers. Put 25.11's first for every target in this
# build. Nothing at link time would catch the mix: Python::Module links with
# -undefined dynamic_lookup, so check-delegate-linkage.sh checks the symbols.
include_directories(BEFORE SYSTEM ${PXR_INCLUDE_DIRS})

# The devkit only ships RelWithDebInfo configurations of the pxr targets.
# CMake falls back to them for a Release build on its own. Do not set
# CMAKE_MAP_IMPORTED_CONFIG_RELEASE: that disables the fallback to the
# config-less IMPORTED_LOCATION the MoonRay Find*.cmake targets use.

# --- prebuilt MoonRay core -------------------------------------------------------
find_package(SceneRdl2 REQUIRED)
find_package(ArrasCore REQUIRED)
find_package(McrtMessages REQUIRED)
find_package(McrtDataio REQUIRED)
find_package(Moonshine REQUIRED)
find_package(Moonray REQUIRED)

# scene_rdl2's install omits common/arm/ (emulation.h, sse2neon.h, avx2neon.h),
# which common/math/Math.h includes on arm64 (NOTES/GOTCHAS.md §9). Expose it
# from the source tree under its installed name.
set(_armInclude "${CMAKE_BINARY_DIR}/scene_rdl2-arm-include")
file(MAKE_DIRECTORY "${_armInclude}/scene_rdl2/common")
file(CREATE_LINK "${MOONRAY_SOURCE_ROOT}/moonray/scene_rdl2/lib/common/arm"
     "${_armInclude}/scene_rdl2/common/arm" SYMBOLIC)
set_property(TARGET SceneRdl2::common_math APPEND PROPERTY
    INTERFACE_INCLUDE_DIRECTORIES "${_armInclude}")

# --- the Hydra layer -------------------------------------------------------------
set(HDMOONRAY_BUILD_CMD OFF CACHE BOOL "" FORCE)
set(HDMOONRAY_BUILD_DEBUG_PLUGIN OFF CACHE BOOL "" FORCE)
set(HDMOONRAY_BUILD_HOUDINI OFF CACHE BOOL "" FORCE)

# Absolute RPATHs to the prebuilt core and deps, so the delegate resolves them
# from inside Maya without DYLD_* variables. Maya's USD is not reached through
# RPATH at all; see PinMayaUsd.cmake below.
#
# Order matters: the core install holds a USD 22.11 build of libhydramoonray
# under the same name, so hdMoonray's @loader_path entries (which it prepends)
# must be searched first. Two CMake defaults would break that order:
#  - on macOS, installing rewrites the build RPATH with install_name_tool,
#    keeping entries common to both lists in their build position and appending
#    the rest, so @loader_path ends up last. Linking with the install RPATH
#    directly avoids the rewrite (build-tree binaries are never run).
#  - link-path RPATHs would add the shim's lib dir; hdMoonray honours this off.
set(_depsLib "$ENV{DEPS_ROOT}/lib")
set(CMAKE_INSTALL_RPATH "${MOONRAY_INSTALL_DIR}/lib" "${_depsLib}")
set(CMAKE_BUILD_WITH_INSTALL_RPATH TRUE)
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH FALSE)

set(_hydra "${MOONRAY_SOURCE_ROOT}/moonray/hydra")
add_subdirectory("${_hydra}/hdMoonray" hdMoonray)
add_subdirectory("${_hydra}/moonray_sdr_plugins" moonray_sdr_plugins)

# Runs after the subdirectories' install rules (policy CMP0082).
file(REAL_PATH "${PXR_USD_LOCATION}/lib" _mayaUsdLib)
install(CODE "
    set(INSTALL_PREFIX \"\${CMAKE_INSTALL_PREFIX}\")
    set(MAYA_USD_LIB \"${_mayaUsdLib}\")
    set(SHIM_LIB \"${PXR_USD_LOCATION}/lib\")
    set(WORKSPACE_LIB_DIRS \"${MOONRAY_INSTALL_DIR}/lib;${_depsLib};\${CMAKE_INSTALL_PREFIX}/lib\")
    include(\"${CMAKE_CURRENT_SOURCE_DIR}/cmake/PinMayaUsd.cmake\")
")
