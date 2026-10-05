# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Headless M2 check: does Maya itself find the delegate through the module?

Run with mayapy and only MAYA_MODULE_PATH pointing at make-maya-module.sh's
output (check-maya-module.sh does this). Starts maya.standalone, loads
mayaUsdPlugin and mayaHydra, and checks that mayaHydra lists
HdMoonrayRendererPlugin with display name "Moonray" — the entry that becomes
the viewport's Renderer menu item.
"""
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402

failures = []
for env in ("MAYA_PXR_PLUGINPATH_NAME", "MOONRAY_CLASS_PATH", "RDL2_DSO_PATH", "ARRAS_SESSION_PATH"):
    value = os.environ.get(env, "")
    print(f"{env} = {value}")
    if not value:
        failures.append(f"{env} not set by the module")

for plugin in ("mayaUsdPlugin", "mayaHydra"):
    cmds.loadPlugin(plugin, quiet=True)
    print(f"{plugin}: loaded={bool(cmds.pluginInfo(plugin, query=True, loaded=True))}")

renderers = cmds.mayaHydra(listRenderers=True) or []
print("mayaHydra renderers:", renderers)
if "HdMoonrayRendererPlugin" not in renderers:
    failures.append("HdMoonrayRendererPlugin not listed by mayaHydra")
else:
    name = cmds.mayaHydra(getRendererDisplayName=True, renderer="HdMoonrayRendererPlugin")
    print("display name:", name)
    if name != "Moonray":
        failures.append(f"display name {name!r}, want 'Moonray'")

from pxr import Plug  # noqa: E402  (Maya's USD, after mayaUsd set up plugin paths)
plugin = Plug.Registry().GetPluginWithName("hd_moonray")
print("hd_moonray plugin:", plugin and plugin.path)

maya.standalone.uninitialize()
if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("RESULT: PASS")
