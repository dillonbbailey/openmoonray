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
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(width=2, height=2, depth=2, name="m3Cube")[0]
        cmds.setAttr(cube + ".rotateY", 30)
        cameras = {p: cmds.modelEditor(p, query=True, camera=True)
                   for p in cmds.getPanel(type="modelPanel")}
        say(f"model panels: {cameras}")
        # The camera may come back as "persp", "|persp" or "perspShape".
        panel = next(p for p, cam in cameras.items()
                     if cam.split("|")[-1] in ("persp", "perspShape"))
        state["panel"] = panel
        cmds.setFocus(panel)
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
