# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
#
# Install-time fixup, run by hydra-only/CMakeLists.txt after everything is
# installed. Inputs: INSTALL_PREFIX, MAYA_USD_LIB (real path), SHIM_LIB,
# WORKSPACE_LIB_DIRS (list).
#
# Why: the workspace deps dir holds USD 22.11 as @rpath/libusd_*.dylib, the
# same install names as Maya's USD 25.11, while Maya's USD dir holds boost
# 1.88 under the same names as the deps boost 1.78 the MoonRay core needs. No
# single RPATH order serves both. So:
#   1. every dependency only Maya provides (all libusd_*, OpenSubdiv 3.7, ...)
#      is rewritten to its absolute path in Maya's install — inside Maya that is
#      the very file Maya already loaded, so dyld reuses it;
#   2. RPATH entries pointing into Maya's USD dir or the shim are removed, so
#      every other @rpath dependency resolves from the workspace only;
#   3. binaries are ad-hoc re-signed (install_name_tool invalidates signatures).

file(GLOB_RECURSE _binaries LIST_DIRECTORIES false
    "${INSTALL_PREFIX}/lib/*.dylib" "${INSTALL_PREFIX}/plugin/*.dylib")

foreach(_bin IN LISTS _binaries)
    if(IS_SYMLINK "${_bin}")
        continue()
    endif()
    execute_process(COMMAND otool -L "${_bin}" OUTPUT_VARIABLE _out COMMAND_ERROR_IS_FATAL ANY)
    execute_process(COMMAND otool -D "${_bin}" OUTPUT_VARIABLE _id COMMAND_ERROR_IS_FATAL ANY)
    string(REGEX MATCH "[^\n]*$" _id "${_id}")
    string(STRIP "${_id}" _id)

    set(_args "")
    string(REGEX MATCHALL "@rpath/[^ \t\n]+" _deps "${_out}")
    list(REMOVE_DUPLICATES _deps)
    foreach(_dep IN LISTS _deps)
        if(_dep STREQUAL _id)
            continue()
        endif()
        string(REPLACE "@rpath/" "" _leaf "${_dep}")
        set(_inWorkspace FALSE)
        foreach(_dir IN LISTS WORKSPACE_LIB_DIRS)
            if(EXISTS "${_dir}/${_leaf}")
                set(_inWorkspace TRUE)
            endif()
        endforeach()
        if(EXISTS "${MAYA_USD_LIB}/${_leaf}" AND (_leaf MATCHES "^libusd_" OR NOT _inWorkspace))
            list(APPEND _args -change "${_dep}" "${MAYA_USD_LIB}/${_leaf}")
        endif()
    endforeach()

    execute_process(COMMAND otool -l "${_bin}" OUTPUT_VARIABLE _load COMMAND_ERROR_IS_FATAL ANY)
    string(REGEX MATCHALL "path [^ \n]+ \\(offset" _rpaths "${_load}")
    foreach(_rp IN LISTS _rpaths)
        string(REGEX REPLACE "^path (.+) \\(offset$" "\\1" _rp "${_rp}")
        if(_rp MATCHES "^${SHIM_LIB}" OR _rp MATCHES "^${MAYA_USD_LIB}")
            list(APPEND _args -delete_rpath "${_rp}")
        endif()
    endforeach()

    if(_args)
        message(STATUS "Pinning Maya USD in ${_bin}")
        execute_process(COMMAND install_name_tool ${_args} "${_bin}"
                        ERROR_QUIET COMMAND_ERROR_IS_FATAL ANY)
        execute_process(COMMAND codesign --force --sign - "${_bin}"
                        OUTPUT_QUIET ERROR_QUIET COMMAND_ERROR_IS_FATAL ANY)
    endif()
endforeach()
