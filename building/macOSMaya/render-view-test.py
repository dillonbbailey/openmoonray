# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Hydra Moonray in Render View and Render Settings, run inside Maya by
m3-render-test.sh (M3_SCRIPT=render-view-test.py; set HDMOONRAY_RDLA_OUTPUT to
also check the settings MoonRay got).

Uses a Maya project under $M3_OUT (renders must not land in the user's
default project), then:
  1. Render Using = Hydra Moonray, pixel samples 2 via the render globals
  2. Render > Render Current Frame: Render View shows a MoonRay image
     (moonray_maya.render_view) -> renderview.png; the RDL has pixel_samples 2
  3. Render Settings has a MoonRay tab -> rendersettings.png
  4. the viewport option box uses the same layout -> optionbox.png
Writes $M3_OUT/renderview.log and $M3_OUT/renderview-result.txt.
"""
import os
import re
import time

from maya import cmds, mel
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
RDLA = os.environ.get("HDMOONRAY_RDLA_OUTPUT", "")
RENDERER = "HdMoonrayRendererPlugin"
PROJECT = os.path.join(OUT, "project")

log = open(os.path.join(OUT, "renderview.log"), "w", buffering=1)
start = time.time()
failures = []


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def check(ok, what):
    say(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def guarded(step):
    def run():
        try:
            step()
        except Exception:
            import traceback
            say(f"{step.__name__} ERROR " + traceback.format_exc())
            failures.append(step.__name__)
            finish()
    return run


def grab_window(name, path):
    from maya import OpenMayaUI
    from shiboken6 import wrapInstance
    from PySide6.QtWidgets import QWidget
    ptr = OpenMayaUI.MQtUtil.findWindow(name) or OpenMayaUI.MQtUtil.findControl(name)
    if ptr:
        wrapInstance(int(ptr), QWidget).window().grab().save(path)
    return bool(ptr)


@guarded
def setup():
    cmds.scriptEditorInfo(historyFilename=os.path.join(OUT, "script-editor.txt"), writeHistory=True)
    os.makedirs(PROJECT, exist_ok=True)
    cmds.workspace(PROJECT, newWorkspace=True)
    cmds.workspace(fileRule=["images", "images"])
    cmds.workspace(saveWorkspace=True)
    cmds.workspace(PROJECT, openWorkspace=True)
    say(f"project {cmds.workspace(query=True, rootDirectory=True)}")
    cmds.loadPlugin("mayaHydra", quiet=True)
    cube = cmds.polyCube(name="rvCube")[0]
    cmds.setAttr(cube + ".rotateY", 30)
    plane = cmds.polyPlane(name="rvGround", width=8, height=8)[0]
    cmds.move(0, -0.5, 0, plane)
    light = cmds.directionalLight(name="rvSun", intensity=2)
    cmds.xform(cmds.listRelatives(light, parent=True)[0], rotation=(-50, 30, 0))
    cmds.setAttr("persp.translate", 4, 3, 5)
    cmds.setAttr("persp.rotate", -25, 38, 0)
    cmds.setAttr("defaultRenderGlobals.currentRenderer", RENDERER, type="string")
    cmds.setAttr("defaultResolution.width", 480)
    cmds.setAttr("defaultResolution.height", 270)
    cmds.setAttr("defaultResolution.deviceAspectRatio", 480 / 270.0)
    from moonray_maya import render_settings
    render_settings.ensure_globals()
    cmds.setAttr(render_settings.plug("pixelSamples"), 2)
    cmds.setAttr("defaultRenderGlobals.imageFilePrefix", "userPrefix", type="string")
    QtCore.QTimer.singleShot(1000, render)


@guarded
def render():
    # The hook runs deferred after mayaHydra loads.
    check(cmds.renderer(RENDERER, query=True, renderProcedure=True) == "moonrayRenderViewRender",
          "Hydra Moonray's render procedure is moonrayRenderViewRender")
    t = time.time()
    mel.eval("RenderIntoNewWindow")  # Render > Render Current Frame
    say(f"render returned after {time.time() - t:.1f}s")
    QtCore.QTimer.singleShot(2000, check_render_view)


@guarded
def check_render_view():
    images = cmds.renderWindowEditor("renderView", query=True, nbImages=True)
    say(f"Render View images: {images}")
    path = os.path.join(OUT, "renderview.png")
    format_before = cmds.getAttr("defaultRenderGlobals.imageFormat")
    cmds.setAttr("defaultRenderGlobals.imageFormat", 32)  # writeImage's file format
    cmds.renderWindowEditor("renderView", edit=True, writeImage=path)
    cmds.setAttr("defaultRenderGlobals.imageFormat", format_before)
    check(os.path.exists(path) and os.path.getsize(path) > 2000, "Render View shows the MoonRay frame (renderview.png)")
    grab_window("renderViewWindow", os.path.join(OUT, "renderview-window.png"))
    tmp = os.path.join(PROJECT, "images", "tmp")
    files = sorted(os.listdir(tmp)) if os.path.isdir(tmp) else []
    check(any(f.startswith("moonray_persp") for f in files), f"frame written to the project's images/tmp: {files}")
    check(cmds.getAttr("defaultRenderGlobals.imageFilePrefix") == "userPrefix"
          and cmds.workspace(fileRuleEntry="images") == "images",
          "render settings restored after the Render View frame")
    if RDLA:
        text = open(RDLA).read() if os.path.exists(RDLA) else ""
        found = re.search(r'\["pixel_samples"\] = ([^,]+),', text)
        check(found is not None and found.group(1).strip() == "2", "Render View frame used pixel_samples 2")
    mel.eval("unifiedRenderGlobalsWindow")
    QtCore.QTimer.singleShot(2000, check_render_settings)


@guarded
def check_render_settings():
    tabs = cmds.renderer(RENDERER, query=True, globalsTabLabels=True) or []
    check("MoonRay" in tabs, f"Render Settings tabs {tabs}")
    from maya import OpenMayaUI
    from shiboken6 import wrapInstance
    from PySide6.QtWidgets import QTabWidget, QWidget
    window = wrapInstance(int(OpenMayaUI.MQtUtil.findWindow("unifiedRenderGlobalsWindow")), QWidget)
    # As clicking the tab does (its preSelectCommand fills it).
    layout = f"{RENDERER}TabLayout"
    labels = cmds.tabLayout(layout, query=True, tabLabel=True)
    cmds.tabLayout(layout, edit=True, selectTabIndex=labels.index("MoonRay") + 1)
    mel.eval("fillSelectedTabForCurrentRenderer")
    window.resize(560, 900)
    QtCore.QTimer.singleShot(1500, lambda: guarded(after_tab)())


def after_tab():
    from moonray_maya import render_settings
    from maya import OpenMayaUI
    from shiboken6 import wrapInstance
    from PySide6.QtWidgets import QWidget
    window = wrapInstance(int(OpenMayaUI.MQtUtil.findWindow("unifiedRenderGlobalsWindow")), QWidget)
    window.grab().save(os.path.join(OUT, "rendersettings.png"))
    check(bool(render_settings._controls), f"MoonRay tab built {len(render_settings._controls)} controls")
    cmds.deleteUI("unifiedRenderGlobalsWindow")
    mel.eval(f"mayaHydraRenderOverride_{RENDERER}OptionBox()")
    QtCore.QTimer.singleShot(1500, lambda: guarded(check_option_box)())


def check_option_box():
    window = f"mayaHydraRenderOverride_{RENDERER}OptionsWindow"
    check(grab_window(window, os.path.join(OUT, "optionbox.png")), "option box opened (optionbox.png)")
    from moonray_maya import render_settings
    labels = [cmds.optionMenuGrp(c, query=True, label=True) for c in render_settings._controls.values()
              if cmds.optionMenuGrp(c, exists=True)]
    check("Sampling" in labels, f"option box uses the MoonRay layout (menus {labels})")
    finish()


def finish():
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    say(verdict)
    open(os.path.join(OUT, "renderview-result.txt"), "w").write(verdict + "\n")
    QtCore.QTimer.singleShot(1000, lambda: cmds.quit(force=True))


QtCore.QTimer.singleShot(int(os.environ.get("M3_SETUP_DELAY_MS", "15000")), setup)
