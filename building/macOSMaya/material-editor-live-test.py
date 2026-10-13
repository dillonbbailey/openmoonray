# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Assign a Material Editor material while Hydra Moonray renders, run inside
Maya by m3-render-test.sh (M3_SCRIPT=material-editor-live-test.py).

Loads testdata/materials/grouped.usda as a mayaUsd stage, switches the persp
panel to Hydra Moonray and lets it render, then selects the Xform /statue and
assigns a bright red material from the material graphs list's right-click menu.
The sphere inside already has its own material, as models imported from Maya
do. With Override materials below off it keeps it; with it on (the default),
assigning to the group reaches it. Checks that the sphere resolves
to the new material, that the delegate syncs it (hdm.log), and that the
viewport turns red. Writes
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
    "testdata", "materials", "grouped.usda")
SELECT = os.environ.get("EDITOR_SELECT", "/statue")
GEOMETRY = os.environ.get("EDITOR_GEOMETRY", "/statue/body")
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


def geometry_material():
    from pxr import UsdShade
    material, _ = UsdShade.MaterialBindingAPI(stage().GetPrimAtPath(GEOMETRY)).ComputeBoundMaterial()
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
    cmds.select(state["proxy"] + "," + SELECT, replace=True)
    from moonray_material_editor import maya_host
    editor = state["editor"] = maya_host.show()
    editor.usd_viewer.event_received.connect(lambda e: state["events"].append((e["event"], e.get("message"))))
    bridge = editor.usd_viewer
    wait(lambda: bridge.ready and bridge.selected_prim_paths == [SELECT], step_assign,
         f"editor follows Maya selection (stage + {SELECT})")


def set_override(on):
    # Signals blocked: the editor would save the choice in the user's settings.
    action = state["editor"].override_descendants_action
    with QtCore.QSignalBlocker(action):
        action.setChecked(on)


def assign_from_menu(then):
    editor = state["editor"]
    listing = editor.material_graphs
    row = next(i for i in range(listing.count())
               if listing.item(i).data(QtCore.Qt.ItemDataRole.UserRole) == state["doc"].id)
    listing.setCurrentRow(row)
    menu = editor.material_graph_menu()
    action = next((a for a in menu.actions() if a.text() == MENU_TEXT), None)
    override = next((a for a in menu.actions() if a.text() == "Override materials below"), None)
    check(action is not None, "material graph right-click menu has the assign item")
    check(bool(override and override.isCheckable()), "right-click menu has the Override materials below option")
    check(bool(action and action.isEnabled()), f"assign item is enabled with {SELECT} selected")
    bound = sum(e == "material_bound" for e, _ in state["events"])
    if action:
        action.trigger()
    menu.deleteLater()
    wait(lambda: sum(e == "material_bound" for e, _ in state["events"]) > bound, then,
         f"material bound to {SELECT}")


def step_assign():
    editor = state["editor"]
    state["override_was"] = editor.override_descendants_action.isChecked()
    editor.new_graph()
    doc = state["doc"] = editor.document
    node = next(n for n in doc.graph.data["nodes"] if n["shader"] == "DwaBaseMaterial")
    node["values"]["albedo"] = [1.0, 0.0, 0.0]
    editor.update_material_graphs()
    set_override(False)
    say("Override materials below: off")
    assign_from_menu(step_kept)


def step_kept():
    material = geometry_material()
    path = str(material.GetPath()) if material else ""
    check(path == "/Looks/Stone", f"option off: {GEOMETRY} keeps its own material ({path})")
    message = state["editor"].statusBar().currentMessage()
    check("own material (Tools > Override materials below is off)" in message, f"option off: status bar says so ({message!r})")
    set_override(True)
    say("Override materials below: on")
    assign_from_menu(step_bound)


def step_bound():
    material = geometry_material()
    state["material"] = str(material.GetPath()) if material else ""
    check(state["material"].startswith("/ShaderGraphMaterials/"),
          f"option on: {GEOMETRY} resolves to the assigned material ({state['material']})")
    message = state["editor"].statusBar().currentMessage()
    check("overrides the own materials" in message, f"option on: status bar says so ({message!r})")
    QtCore.QTimer.singleShot(12000, step_check)


def step_check():
    after = capture("after")
    text = hdm_log()[state["log_mark"]:]
    syncs = [line for line in text.splitlines() if line.startswith("SyncStart")]
    say("delegate syncs after assigning:\n  " + "\n  ".join(syncs[:40]))
    check(any(state["material"] in line for line in syncs), "delegate synced the new material")
    check(any(GEOMETRY in line for line in syncs), f"delegate re-synced {GEOMETRY}")
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
    if "override_was" in state:
        set_override(state["override_was"])
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
