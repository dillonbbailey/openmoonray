# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Export the calibration scenes to Arnold .ass files (render them with kick).

Run with mayapy:  mayapy export-arnold.py <dir>   (reads/writes <dir>/<light>.ma/.ass)
"""
import glob
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402

cmds.loadPlugin("mtoa", quiet=True)
for scene in sorted(glob.glob(os.path.join(sys.argv[1], "*.ma"))):
    cmds.file(scene, open=True, force=True)
    cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
    from mtoa.core import createOptions
    createOptions()
    cmds.setAttr("defaultResolution.width", 600)
    cmds.setAttr("defaultResolution.height", 400)
    cmds.setAttr("defaultArnoldRenderOptions.AASamples", 4)
    ass = scene[:-3] + ".ass"
    cmds.arnoldExportAss(filename=ass, cam="perspShape", lightLinks=True, shadowLinks=True)
    print(f"exported {ass}")

maya.standalone.uninitialize()
