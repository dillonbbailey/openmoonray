# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
# Adapted from the MoonRay Rocky9 recipe and Ubuntu24 PR #226.
cmake_minimum_required(VERSION 3.23.1)
project(moonray_local_dependencies LANGUAGES C CXX)
include(ExternalProject)
set(InstallRoot "$ENV{DEPS_ROOT}" CACHE PATH "Workspace-local dependency prefix")
if(NOT IS_ABSOLUTE "${InstallRoot}")
  message(FATAL_ERROR "InstallRoot must be an absolute local prefix")
endif()
set(COMMON
  -DCMAKE_INSTALL_PREFIX:PATH=${InstallRoot}
  -DCMAKE_PREFIX_PATH:PATH=${InstallRoot}
  -DCMAKE_INSTALL_LIBDIR=lib
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_C_COMPILER:FILEPATH=${CMAKE_C_COMPILER}
  -DCMAKE_CXX_COMPILER:FILEPATH=${CMAKE_CXX_COMPILER}
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  -DCMAKE_INSTALL_RPATH:PATH=${InstallRoot}/lib
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF
  -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF
  -DPYTHON_EXECUTABLE:FILEPATH=${InstallRoot}/bin/python
  -DPython_EXECUTABLE:FILEPATH=${InstallRoot}/bin/python
  -DPython3_EXECUTABLE:FILEPATH=${InstallRoot}/bin/python
  -DPython_ROOT_DIR:PATH=${InstallRoot}
  -DBUILD_TESTING=OFF)
function(dep NAME REPO TAG)
  ExternalProject_Add(${NAME}
    URL https://codeload.github.com/${REPO}/tar.gz/${TAG}
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${COMMON} ${ARGN}
    DEPENDS ${CHAIN})
  set(CHAIN ${NAME} PARENT_SCOPE)
endfunction()

# Versions follow building/Rocky9 and Ubuntu PR #226; TBB is built first
# so Embree, OpenVDB, OIDN and USD all use the same ABI.
ExternalProject_Add(TBB
  URL https://codeload.github.com/oneapi-src/oneTBB/tar.gz/v2020.3.3
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  BUILD_IN_SOURCE TRUE
  CONFIGURE_COMMAND ""
  BUILD_COMMAND make -j16 tbb tbbmalloc compiler=gcc arch=intel64
    "CPLUS=${CMAKE_CXX_COMPILER}" "CONLY=${CMAKE_C_COMPILER}"
  INSTALL_COMMAND bash -c "mkdir -p '${InstallRoot}/lib' '${InstallRoot}/include' && cp -a build/*_release/libtbb* '${InstallRoot}/lib/' && cp -a include/tbb '${InstallRoot}/include/'")
set(CHAIN TBB)
dep(OpenSubdiv PixarAnimationStudios/OpenSubdiv v3_5_0
  -DNO_PTEX=ON -DNO_OMP=ON -DNO_TBB=ON -DNO_CUDA=ON -DNO_OPENCL=ON
  -DNO_CLEW=ON -DNO_DOC=ON -DNO_REGRESSION=ON -DNO_EXAMPLES=ON
  -DNO_TUTORIALS=ON -DNO_GLTESTS=ON -DNO_TESTS=ON)
dep(OpenEXR AcademySoftwareFoundation/openexr v3.1.8
  -DBUILD_SHARED_LIBS=ON -DOPENEXR_BUILD_TOOLS=OFF -DOPENEXR_INSTALL_TOOLS=OFF)
ExternalProject_Add(Random123
  URL https://codeload.github.com/DEShawResearch/random123/tar.gz/v1.14.0
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  BUILD_IN_SOURCE TRUE CONFIGURE_COMMAND "" BUILD_COMMAND ""
  INSTALL_COMMAND make install-include prefix=${InstallRoot}
  DEPENDS ${CHAIN})
set(CHAIN Random123)
ExternalProject_Add(ISPC
  URL https://github.com/ispc/ispc/releases/download/v1.21.0/ispc-v1.21.0-linux.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  CONFIGURE_COMMAND "" BUILD_COMMAND ""
  INSTALL_COMMAND ${CMAKE_COMMAND} -E copy <SOURCE_DIR>/bin/ispc ${InstallRoot}/bin/ispc
  DEPENDS ${CHAIN})
set(CHAIN ISPC)
dep(embree embree/embree v4.2.0
  -DEMBREE_ISPC_EXECUTABLE=${InstallRoot}/bin/ispc -DEMBREE_ISPC_SUPPORT=ON
  -DEMBREE_IGNORE_INVALID_RAYS=ON -DEMBREE_RAY_MASK=ON -DEMBREE_MAX_ISA=AVX512
  -DEMBREE_TUTORIALS=OFF -DTBB_ROOT=${InstallRoot})
dep(OpenColorIO AcademySoftwareFoundation/OpenColorIO v2.2.1
  -DOCIO_BUILD_APPS=OFF -DOCIO_BUILD_TESTS=OFF -DOCIO_BUILD_GPU_TESTS=OFF
  -DOCIO_BUILD_PYTHON=OFF -DOCIO_BUILD_STATIC=OFF -DCMAKE_CXX_STANDARD=17)
dep(OpenVDB AcademySoftwareFoundation/openvdb v9.1.0
  -DOPENVDB_BUILD_PYTHON_MODULE=OFF -DOPENVDB_BUILD_BINARIES=OFF
  -DOPENVDB_BUILD_UNITTESTS=OFF -DOPENVDB_CORE_STATIC=OFF -DUSE_BLOSC=ON
  -DTBB_ROOT=${InstallRoot} -DTbb_INCLUDE_DIR=${InstallRoot}/include
  -DTbb_LEGACY_INCLUDE_DIR=${InstallRoot}/include -DBoost_ROOT=${InstallRoot})
dep(OpenImageIO OpenImageIO/oiio v2.5.19.0
  -DOpenEXR_ROOT=${InstallRoot} -DUSE_QT=OFF -DUSE_PYTHON=ON
  -DCMAKE_CXX_STANDARD=17 -DOIIO_BUILD_TESTS=OFF -DOIIO_BUILD_TOOLS=ON
  -DUSE_FFMPEG=OFF -DUSE_OPENCV=OFF -DUSE_DCMTK=OFF -DUSE_OPENVDB=OFF
  -DUSE_LIBHEIF=OFF -DUSE_LIBRAW=OFF -DUSE_Ptex=OFF)
ExternalProject_Add(OpenImageDenoise
  URL https://github.com/OpenImageDenoise/oidn/releases/download/v2.3.3/oidn-2.3.3.src.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  CMAKE_ARGS ${COMMON} -DISPC_EXECUTABLE=${InstallRoot}/bin/ispc
    -DTBB_ROOT=${InstallRoot} -DOIDN_APPS=OFF -DOIDN_DEVICE_CUDA=OFF -DOIDN_DEVICE_SYCL=OFF
  DEPENDS ${CHAIN})
set(CHAIN OpenImageDenoise)
# USD only requires TBB and OpenSubdiv from this source build. Allow it to
# compile alongside the remaining rendering dependencies under make's jobserver.
set(CHAIN TBB OpenSubdiv)
dep(USD PixarAnimationStudios/OpenUSD v23.08
  -DPXR_ENABLE_PYTHON_SUPPORT=ON -DPXR_USE_PYTHON_3=ON
  -DPXR_PY_UNDEFINED_DYNAMIC_LOOKUP=OFF
  -DPXR_BUILD_TESTS=OFF -DPXR_BUILD_EXAMPLES=OFF -DPXR_BUILD_TUTORIALS=OFF
  -DPXR_BUILD_USD_TOOLS=ON -DPXR_BUILD_USDVIEW=ON
  -DPXR_ENABLE_PTEX_SUPPORT=OFF -DPXR_ENABLE_OPENVDB_SUPPORT=OFF
  -DBoost_NO_BOOST_CMAKE=ON -DBoost_NO_SYSTEM_PATHS=ON
  -DBOOST_ROOT=${InstallRoot} -DTBB_ROOT_DIR=${InstallRoot}
  -DTBB_USE_DEBUG_BUILD=OFF -DTBB_SUPPRESS_DEPRECATED_MESSAGES=ON)
dep(GLFW glfw/glfw 3.4 -DGLFW_BUILD_EXAMPLES=OFF -DGLFW_BUILD_TESTS=OFF
  -DGLFW_BUILD_DOCS=OFF -DGLFW_BUILD_WAYLAND=OFF -DBUILD_SHARED_LIBS=ON)
