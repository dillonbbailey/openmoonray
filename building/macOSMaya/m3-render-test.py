# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""M3 GUI test: render a cube through Hydra Moonray inside Maya.

Run by m3-render-test.sh as Maya's startup -command. Creates a cube, switches
the persp viewport to the Moonray render override, then captures the viewport
every few seconds and records whether Arras's execComp is running. Writes
everything to $M3_OUT and quits Maya. Runs on Qt timers so Maya's event loop
(and with it the viewport) keeps running between captures.
"""
import os
import subprocess
import time

from maya import cmds, mel, utils
from maya import OpenMayaUI as omui1
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
# M3_OVERRIDE="" leaves the panel on Viewport 2.0, for a baseline capture.
OVERRIDE = os.environ.get("M3_OVERRIDE", "mayaHydraRenderOverride_HdMoonrayRendererPlugin")
# Playblasts render through their own path, not the override's MoonRay image.
PLAYBLAST = os.environ.get("M3_PLAYBLAST", "0") == "1"
# M3_GRAB=0: only watch execComp; do not refresh or read the viewport.
GRAB = os.environ.get("M3_GRAB", "1") == "1"
CAPTURES = int(os.environ.get("M3_CAPTURES", "8"))
INTERVAL_MS = int(os.environ.get("M3_INTERVAL_MS", "5000"))
os.makedirs(OUT, exist_ok=True)
log = open(os.path.join(OUT, "m3.log"), "w", buffering=1)
start = time.time()
state = {"n": 0, "panel": None}


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def exec_comps():
    ps = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return [l.strip() for l in ps.splitlines() if "execComp" in l and "grep" not in l]


def dump_images(path):
    """Write every library dyld has mapped into Maya: what the delegate pulled in."""
    import ctypes
    libc = ctypes.CDLL(None)
    libc._dyld_get_image_name.restype = ctypes.c_char_p
    names = sorted(os.path.realpath(libc._dyld_get_image_name(i).decode())
                   for i in range(libc._dyld_image_count()))
    with open(path, "w") as f:
        f.write("\n".join(names) + "\n")
    return names


def watch_panel():
    """Log every change to the panel's renderer override, with a timestamp."""
    panel = state["panel"]

    def changed():
        say(f"  modelEditorChanged: override="
            f"{cmds.modelEditor(panel, query=True, rendererOverrideName=True)!r} "
            f"available={cmds.ogsRender(query=True, availableRenderOverrides=True)}")
    cmds.scriptJob(event=["modelEditorChanged", changed], killWithScene=False)


def setup():
    # Script Editor history: Hydra/USD (Tf) errors and warnings land there.
    cmds.scriptEditorInfo(historyFilename=os.path.join(OUT, "script-editor.txt"),
                          writeHistory=True)
    try:
        # M3_EXTRA_PLUGINS="mtoa ...": load more plugins first (bisecting conflicts).
        extra = os.environ.get("M3_EXTRA_PLUGINS", "").split()
        for plugin in extra + ["mayaUsdPlugin", "mayaHydra"]:
            cmds.loadPlugin(plugin, quiet=True)
            say(f"loaded plugin {plugin}")
        # M3_SCENE_FILE: open a saved Maya scene (keeps its camera) instead of building one.
        scene_file = os.environ.get("M3_SCENE_FILE", "")
        if scene_file:
            cmds.file(scene_file, open=True, force=True)
            say(f"opened scene {scene_file}")
        else:
            cmds.file(new=True, force=True)
        usd_file = os.environ.get("M3_USD_FILE", "")
        if scene_file:
            pass
        elif usd_file:
            # A mayaUsd stage instead of a Maya cube (imaged through mayaUsd's scene index).
            shape = cmds.createNode("mayaUsdProxyShape", name="m3UsdShape")
            cmds.setAttr(shape + ".filePath", usd_file, type="string")
            cmds.connectAttr("time1.outTime", shape + ".time")
            say(f"mayaUsd stage: {usd_file}")
        else:
            cube = cmds.polyCube(width=2, height=2, depth=2, name="m3Cube")[0]
            cmds.setAttr(cube + ".rotateY", 30)
            # M3_CUBE_SHADER="openPBRSurface:1,0,0": assign a Maya material with that base colour.
            shader_spec = os.environ.get("M3_CUBE_SHADER", "")
            if shader_spec:
                node_type, _, rgb = shader_spec.partition(":")
                shader = cmds.shadingNode(node_type, asShader=True, name="m3Shader")
                sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="m3ShaderSG")
                cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
                cmds.sets(cube, edit=True, forceElement=sg)
                if rgb:
                    attr = {"openPBRSurface": "baseColor", "standardSurface": "baseColor"}.get(node_type, "color")
                    cmds.setAttr(f"{shader}.{attr}", *[float(c) for c in rgb.split(",")], type="double3")
                say(f"cube material: {node_type} {rgb}")
        # M3_MAYA_LIGHTS="point,spot,directional,area": add Maya lights around the scene.
        for kind in filter(None, os.environ.get("M3_MAYA_LIGHTS", "").split(",")):
            make = {"point": cmds.pointLight, "spot": cmds.spotLight,
                    "directional": cmds.directionalLight}.get(kind)
            if make:
                shape = make(name=f"m3{kind.capitalize()}Light", intensity=1.5)
            else:
                shape = cmds.shadingNode(f"{kind}Light", asLight=True, name=f"m3{kind.capitalize()}Light")
            xform = cmds.listRelatives(shape, parent=True)[0] if cmds.nodeType(shape).endswith("Light") else shape
            cmds.xform(xform, translation=(2, 4, 3), rotation=(-50, 30, 0), worldSpace=True)
            say(f"maya light: {kind} -> {shape}")
        cameras = {p: cmds.modelEditor(p, query=True, camera=True)
                   for p in cmds.getPanel(type="modelPanel")}
        say(f"model panels: {cameras}")
        # The camera may come back as "persp", "|persp" or "perspShape".
        panel = next(p for p, cam in cameras.items()
                     if cam.split("|")[-1] in ("persp", "perspShape"))
        state["panel"] = panel
        cmds.setFocus(panel)
        if not scene_file:
            cmds.viewFit("persp", all=True)
        watch_panel()
    except Exception as e:
        say(f"SETUP ERROR: {e!r}")
    # MtoA answers NewSceneOpened with evalDeferred("cmds.ActivateViewport20()"),
    # which runs at the next idle and switches the focused panel back to
    # Viewport 2.0 - once Moonray is rendering that can be seconds later. Let
    # the deferred queue drain first.
    cmds.evalDeferred(lambda: QtCore.QTimer.singleShot(2000, switch), lowestPriority=True)


def switch():
    panel = state["panel"]
    try:
        # As the viewport's Renderer menu does it: renderer vp2 + override.
        mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "{OVERRIDE}" "{panel}"')
        say(f"panel {panel}: override = {cmds.modelEditor(panel, query=True, rendererOverrideName=True)}")
    except Exception as e:
        say(f"SWITCH ERROR: {e!r}")
    QtCore.QTimer.singleShot(INTERVAL_MS, capture)


def capture():
    n = state["n"]
    state["n"] += 1
    panel = state["panel"]
    try:
        procs = exec_comps()
        say(f"capture {n}: execComp processes: {len(procs)}" + "".join(f"\n    {p}" for p in procs))
        if n == 1:
            names = dump_images(os.path.join(OUT, "images.txt"))
            ours = [x for x in names if "/local-build/" in x]
            say(f"  images: {len(names)} total, {len(ours)} from the workspace (images.txt)")
        if panel:
            say(f"  override={cmds.modelEditor(panel, query=True, rendererOverrideName=True)!r} "
                f"active={cmds.mayaHydra(listActiveRenderers=True)} "
                f"focus={cmds.getPanel(withFocus=True)}")
        if panel and GRAB:
            # refresh -currentView -filename saves what the viewport presents.
            # M3dView.readColorBuffer comes back empty (all zero) for Hydra
            # overrides that supply real depth - Storm included.
            cmds.setFocus(panel)
            base = os.path.join(OUT, f"view-{n:02d}")
            cmds.refresh(currentView=True, force=True, filename=base, fileExtension="png")
            say(f"  saved view-{n:02d}.png")
            if PLAYBLAST:
                cmds.playblast(frame=[cmds.currentTime(query=True)], format="image", compression="png",
                               completeFilename=os.path.join(OUT, f"playblast-{n:02d}.png"),
                               viewer=False, showOrnaments=False, percent=100, editorPanelName=panel,
                               forceOverwrite=True)
                say(f"  saved playblast-{n:02d}.png")
    except Exception as e:
        say(f"CAPTURE ERROR: {e!r}")
    if state["n"] < CAPTURES:
        QtCore.QTimer.singleShot(INTERVAL_MS, capture)
    else:
        QtCore.QTimer.singleShot(1000, finish)


def finish():
    if state["panel"]:
        cmds.modelEditor(state["panel"], edit=True, rendererOverrideName="")
    say(f"after switching back: execComp processes: {len(exec_comps())}")
    QtCore.QTimer.singleShot(5000, quit_maya)


def quit_maya():
    say(f"quitting; execComp processes: {len(exec_comps())}")
    log.close()
    cmds.quit(force=True)


# Maya resets model panels to Viewport 2.0 while it finishes starting up
# (~15 s after the startup -command runs on this machine), which tears down any
# render override set earlier. Wait that out before switching to Moonray.
SETUP_DELAY_MS = int(os.environ.get("M3_SETUP_DELAY_MS", "30000"))
utils.executeDeferred(lambda: QtCore.QTimer.singleShot(SETUP_DELAY_MS, setup))
