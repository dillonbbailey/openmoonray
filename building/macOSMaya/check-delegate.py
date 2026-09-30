# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""mayapy smoke test for the delegate built by build-delegate.sh.

Loads every plugin in <install>/plugin/pxr into mayapy (Maya's USD 25.11 and
Python 3.13), then inspects the images dyld actually loaded:
  - no USD 22.11 from the workspace deps, no Python 3.9
  - no library from the USD 22.11 core install that this build also installs
    (libhydramoonray): same install name, so a wrong RPATH order picks it up
  - HdMoonrayRendererPlugin registered as an HdRendererPlugin

Usage: mayapy check-delegate.py <maya install dir> <core install dir> <deps dir>
Needs MayaUSD's USD python dir on PYTHONPATH (see check-delegate.sh).
"""
import ctypes
import os
import sys

from pxr import Plug, Tf

install, core, deps = (os.path.realpath(p) for p in sys.argv[1:4])
failures = []

plugins = Plug.Registry().RegisterPlugins(os.path.join(install, "plugin", "pxr"))
names = sorted(p.name for p in plugins)
print("registered:", names)
expected = ["hdMoonrayAdapters", "hd_moonray", "moonrayShaderDiscovery", "moonrayShaderParser"]
if names != expected:
    failures.append(f"registered {names}, want {expected}")
for plugin in plugins:
    try:
        plugin.Load()
    except Tf.ErrorException as e:
        failures.append(f"{plugin.name} failed to load: {e}")
    print(f"  {plugin.name}: loaded={plugin.isLoaded}")

rendererType = Tf.Type.FindByName("HdMoonrayRendererPlugin")
if rendererType.isUnknown or not rendererType.IsA(Tf.Type.FindByName("HdRendererPlugin")):
    failures.append("HdMoonrayRendererPlugin is not a registered HdRendererPlugin")
else:
    print("HdMoonrayRendererPlugin: registered HdRendererPlugin")

libc = ctypes.CDLL(None)
libc._dyld_get_image_name.restype = ctypes.c_char_p
images = [os.path.realpath(libc._dyld_get_image_name(i).decode())
          for i in range(libc._dyld_image_count())]
ownLibs = set(os.listdir(os.path.join(install, "lib")))
for image in images:
    base = os.path.basename(image)
    if image.startswith(deps + "/") and base.startswith("libusd_"):
        failures.append(f"loaded USD 22.11 from deps: {image}")
    if "Python3.framework/Versions/3.9" in image:
        failures.append(f"loaded Python 3.9: {image}")
    if image.startswith(core + "/") and base in ownLibs:
        failures.append(f"loaded the core install's {base} instead of this build's: {image}")

def origin(name):
    hits = sorted({i for i in images if os.path.basename(i).startswith(name)})
    return ", ".join(hits) or "not loaded"

for name in ("libusd_tf.", "libtbb.", "libhydramoonray.", "libscene_rdl2.", "libboost_filesystem."):
    print(f"  {name:22s} {origin(name)}")

if failures:
    print("RESULT: FAIL")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("RESULT: PASS")
