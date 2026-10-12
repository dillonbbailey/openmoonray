# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Assign a Material Editor material while Hydra Moonray renders, run inside
Maya by m3-render-test.sh (M3_SCRIPT=material-editor-live-test.py).

Loads testdata/lights/sphere.usda as a mayaUsd stage, switches the persp panel
to Hydra Moonray and lets it render, then selects /ball and assigns a bright
red material from the material graphs list's right-click menu. Checks that the
binding is authored, that the delegate syncs the new material and /ball
(hdm.log), and that the viewport image changes. Writes
$M3_OUT/editor-live.log, before.png / after.png and editor-live-result.txt.
"""
import os
import time

from maya import cmds, mel
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
HDM_LOG = os.environ.get("HDM_LOG_FILE", os.path.join(OUT, "hdm.log"))
USD_FILE = os.environ.get("EDITOR_USD_FILE") or os.path.join(
    os.environ.get("M3_SCRIPTS_DIR", "/Users/dillonb/DEV/moonray_maya/openmoonray/building/macOSMaya"),
    "testdata", "lights", "sphere.usda")
OVERRIDE = "mayaHydraRenderOverride_HdMoonrayRendererPlugin"
MENU_TEXT = "Assign material to selected USD prims"

log = open(os.path.join(OUT, "editor-live.log"), "w", buffering=1)
start = time.time()
failures = []
state = {"events": []}


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def check(ok, what):
    say(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def wait(condition, then, what, timeout=30.0):
    deadline = time.time() + timeout

    def poll():
        try:
            done = condition()
        except Exception as exc:
            done = False
            say(f"  ({what}: {exc!r})")
        if done:
            try:
                then()
            except Exception:
                import traceback
                say("STEP ERROR " + traceback.format_exc())
                failures.append(then.__name__)
                finish()
        elif time.time() > deadline:
            check(False, what + " (timed out)")
            finish()
        else:
            QtCore.QTimer.singleShot(250, poll)
    poll()


def stage():
    import mayaUsd.ufe
    return mayaUsd.ufe.getStage(state["proxy"])


def ball_material():
    from pxr import UsdShade
    material, _ = UsdShade.MaterialBindingAPI(stage().GetPrimAtPath("/ball")).ComputeBoundMaterial()
    return material


def hdm_log():
    return open(HDM_LOG).read() if os.path.exists(HDM_LOG) else ""


def capture(name):
    # Maya writes the file as given, without adding the extension.
    path = os.path.join(OUT, name + ".png")
    cmds.refresh(currentView=True, force=True, filename=path, fileExtension="png")
    return path


def setup():
    cmds.scriptEditorInfo(historyFilename=os.path.join(OUT, "script-editor.txt"), writeHistory=True)
    try:
        cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        cmds.loadPlugin("mayaHydra", quiet=True)
        transform = cmds.createNode("transform", name="testStage")
        shape = cmds.createNode("mayaUsdProxyShape", name="testStageShape", parent=transform)
        cmds.setAttr(shape + ".filePath", USD_FILE, type="string")
        cmds.connectAttr("time1.outTime", shape + ".time")
        state["proxy"] = cmds.ls(shape, long=True)[0]
        say(f"stage {USD_FILE} -> {state['proxy']}")
        panel = next(p for p in cmds.getPanel(type="modelPanel")
                     if cmds.modelEditor(p, query=True, camera=True).split("|")[-1] in ("persp", "perspShape"))
        cmds.setFocus(panel)
        cmds.viewFit("persp", all=True)
        state["panel"] = panel
        mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "{OVERRIDE}" "{panel}"')
        say("Hydra Moonray on; letting it render")
        QtCore.QTimer.singleShot(12000, step_editor)
    except Exception:
        import traceback
        say("SETUP ERROR " + traceback.format_exc())
        failures.append("setup")
        finish()


def step_editor():
    state["before"] = capture("before")
    state["log_mark"] = len(hdm_log())
    cmds.select(state["proxy"] + ",/ball", replace=True)
    from moonray_material_editor import maya_host
    editor = state["editor"] = maya_host.show()
    editor.usd_viewer.event_received.connect(lambda e: state["events"].append((e["event"], e.get("message"))))
    bridge = editor.usd_viewer
    wait(lambda: bridge.ready and bridge.selected_prim_paths == ["/ball"], step_assign,
         "editor follows Maya selection (stage + /ball)")


def step_assign():
    editor = state["editor"]
    editor.new_graph()
    doc = editor.document
    node = next(n for n in doc.graph.data["nodes"] if n["shader"] == "DwaBaseMaterial")
    node["values"]["albedo"] = [1.0, 0.0, 0.0]
    editor.update_material_graphs()
    listing = editor.material_graphs
    row = next(i for i in range(listing.count())
               if listing.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == doc.id)
    listing.setCurrentRow(row)
    menu = editor.material_graph_menu()
    action = next((a for a in menu.actions() if a.text() == MENU_TEXT), None)
    check(action is not None, "material graph right-click menu has the assign item")
    check(bool(action and action.isEnabled()), "assign item is enabled with /ball selected")
    if action:
        action.trigger()
    menu.deleteLater()
    wait(lambda: any(e == "material_bound" for e, _ in state["events"]) and ball_material(), step_bound,
         "material bound to /ball")


def step_bound():
    state["material"] = str(ball_material().GetPath())
    say(f"bound {state['material']}; events {state['events']}")
    QtCore.QTimer.singleShot(12000, step_check)


def step_check():
    after = capture("after")
    text = hdm_log()[state["log_mark"]:]
    syncs = [line for line in text.splitlines() if line.startswith("SyncStart")]
    say("delegate syncs after assigning:\n  " + "\n  ".join(syncs[:40]))
    check(any(state["material"] in line for line in syncs), "delegate synced the new material")
    check(any("/ball" in line for line in syncs), "delegate re-synced /ball")
    try:
        import numpy as np
        from PySide6.QtGui import QImage

        def pixels(path):
            image = QImage(path).convertToFormat(QImage.Format.Format_RGB888)
            data = np.frombuffer(image.constBits(), np.uint8, image.sizeInBytes())
            return data.reshape(image.height(), image.bytesPerLine())[:, :image.width() * 3].reshape(
                image.height(), image.width(), 3).astype(float)
        a, b = pixels(state["before"]), pixels(after)
        redder = ((b[..., 0] - b[..., 1]) - (a[..., 0] - a[..., 1]) > 40).mean()
        say(f"pixels that turned red: {redder:.3%}")
        check(redder > 0.002, "viewport shows the red material")
    except Exception as exc:
        check(False, f"image comparison ({exc!r})")
    finish()


def finish():
    if state.get("finished"):
        return
    state["finished"] = True
    say(f"editor events: {state['events']}")
    if state.get("editor"):
        say(f"editor status bar: {state['editor'].statusBar().currentMessage()!r}")
    if state.get("panel"):
        mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "" "{state["panel"]}"')
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    say(verdict)
    open(os.path.join(OUT, "editor-live-result.txt"), "w").write(verdict + "\n")
    QtCore.QTimer.singleShot(1500, lambda: cmds.quit(force=True))


QtCore.QTimer.singleShot(int(os.environ.get("M3_SETUP_DELAY_MS", "15000")), setup)
