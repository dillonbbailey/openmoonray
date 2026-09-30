# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# The installed ArrasCoreConfig.cmake requires Libatomic on every Unix, but
# macOS has no separate libatomic (atomics live in compiler-rt) and no exported
# MoonRay target links it. The monorepo build never runs that config, so it
# never noticed. Report it found, with an empty interface target.

if(APPLE)
    set(Libatomic_FOUND TRUE)
    if(NOT TARGET Libatomic::Libatomic)
        add_library(Libatomic::Libatomic INTERFACE IMPORTED)
    endif()
else()
    include("${MOONRAY_SOURCE_ROOT}/cmake_modules/cmake/FindLibatomic.cmake")
endif()
