# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Write testdata/maya/native-geometry.ma: Maya-native geometry for checking
what mayaHydra hands the Moonray delegate. Run with mayapy:
  mayapy make-native-geometry.py <out.ma>
"""
import sys
import maya.standalone
maya.standalone.initialize()
from maya import cmds

cmds.file(new=True, force=True)
# NURBS curve and surface (mayaHydra tessellates/converts these itself).
cmds.curve(name="nurbsCurve", degree=3,
           point=[(-6, 0, 0), (-5, 3, 0), (-4, 0, 0), (-3, 3, 0)])
cmds.sphere(name="nurbsSphere", radius=1)
cmds.move(-2, 1, 0, "nurbsSphere")
# Poly mesh plus two transform instances of it.
cmds.polyCube(name="cube")
cmds.move(1, 0.5, 0, "cube")
for i, x in enumerate((2.5, 4)):
    inst = cmds.instance("cube", name=f"cubeInstance{i}")[0]
    cmds.move(x, 0.5, 0, inst)
# Smooth-mesh preview (display smoothness 3) on a second cube.
cmds.polyCube(name="smoothCube")
cmds.move(6, 0.5, 0, "smoothCube")
cmds.displaySmoothness("smoothCube", polygonObject=3)
# Particle-driven instancer of a small sphere.
proto = cmds.polySphere(name="proto", radius=0.2)[0]
cmds.move(0, -100, 0, proto)  # keep the prototype out of frame
particles = cmds.particle(name="dots", position=[(x * 0.8 - 4, 3.5, 0) for x in range(11)])[0]
cmds.particleInstancer(particles, addObject=True, object=proto)
cmds.polyPlane(name="ground", width=16, height=8)
cam = cmds.camera(name="shotCam")[0]
cmds.move(0, 3, 14, cam)
cmds.rotate(-10, 0, 0, cam)
cmds.file(rename=sys.argv[1])
cmds.file(save=True, type="mayaAscii")
print("wrote", sys.argv[1])
maya.standalone.uninitialize()
