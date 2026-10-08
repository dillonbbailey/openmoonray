# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Write one Maya scene per light type for the Arnold-vs-MoonRay light calibration.

Run with mayapy:  mayapy make-light-scenes.py <outdir>
Each <outdir>/<light>.ma has the same cube (OpenPBR, base colour 0.8 grey, no
specular, so renderer shading differences stay small), the same fixed camera
and exactly one light of the given type with Maya's default settings apart
from its placement.
"""
import os
import sys

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds  # noqa: E402

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

LIGHTS = {
    "point": lambda: cmds.pointLight(name="calibLight"),
    "spot": lambda: cmds.spotLight(name="calibLight"),
    "directional": lambda: cmds.directionalLight(name="calibLight"),
    "area": lambda: cmds.shadingNode("areaLight", asLight=True, name="calibLight"),
}


def light_transform(node):
    if cmds.nodeType(node) == "transform":
        return node
    return cmds.listRelatives(node, parent=True)[0]


for kind, make in LIGHTS.items():
    cmds.file(new=True, force=True)
    cube = cmds.polyCube(width=2, height=2, depth=2, name="calibCube")[0]
    cmds.setAttr(cube + ".rotateY", 30)
    shader = cmds.shadingNode("openPBRSurface", asShader=True, name="calibShader")
    cmds.setAttr(shader + ".baseColor", 0.8, 0.8, 0.8, type="double3")
    cmds.setAttr(shader + ".specularWeight", 0.0)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="calibShaderSG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    cmds.sets(cube, edit=True, forceElement=sg)

    cmds.xform("persp", translation=(6, 4.5, 6), worldSpace=True)
    cmds.viewPlace("perspShape", lookAt=(0, 0, 0))

    light = light_transform(make())
    # 4 units from the cube centre, aimed at it.
    cmds.xform(light, translation=(2.5, 2.5, 2.2), worldSpace=True)
    aim = cmds.aimConstraint(cube, light, aimVector=(0, 0, -1))
    cmds.delete(aim)

    path = os.path.join(OUT, f"{kind}.ma")
    cmds.file(rename=path)
    cmds.file(save=True, type="mayaAscii", force=True)
    print(f"wrote {path}")

maya.standalone.uninitialize()
