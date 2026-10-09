# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Material Editor <-> mayaUsd stage test, run inside Maya by m3-render-test.sh
(M3_SCRIPT=material-editor-usd-test.py; set HDMOONRAY_RDLA_OUTPUT to also
check what reaches MoonRay).

Loads testdata/lights/sphere.usda as a mayaUsd stage, selects /ball and opens
the editor, then step by step:
  1. the editor follows the selection: current stage, selected prim
  2. Tools > Assign material: /ball is bound to a MoonRay material
  3. Maya Undo removes the binding, Redo restores it
  4. editing the graph syncs the USD shader (Auto update)
  5. Select bound prims selects /ball in Maya
  6. opening the USD material from USD SCENE NODES gives a linked tab
  7. Hydra Moonray renders the stage; the RDL has the DwaBaseMaterial
Writes $M3_OUT/editor-usd.log and $M3_OUT/editor-usd-result.txt.
"""
import os
import time

from maya import cmds, mel
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.environ.get("M3_SCRIPTS", "")
USD_FILE = os.environ.get("EDITOR_USD_FILE") or os.path.join(
    os.environ.get("M3_SCRIPTS_DIR", "/Users/dillonb/DEV/moonray_maya/openmoonray/building/macOSMaya"),
    "testdata", "lights", "sphere.usda")
OVERRIDE = "mayaHydraRenderOverride_HdMoonrayRendererPlugin"

log = open(os.path.join(OUT, "editor-usd.log"), "w", buffering=1)
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
        cmds.select(state["proxy"] + ",/ball", replace=True)
        from moonray_material_editor import maya_host
        editor = state["editor"] = maya_host.show()
        editor.usd_viewer.event_received.connect(lambda e: state["events"].append(e["event"]))
        bridge = editor.usd_viewer
        wait(lambda: bridge.ready and bridge.selected_prim_paths == ["/ball"], step_assign,
             "editor follows Maya selection (stage + /ball)")
    except Exception:
        import traceback
        say("SETUP ERROR " + traceback.format_exc())
        failures.append("setup")
        finish()


def step_assign():
    editor = state["editor"]
    check(True, f"current stage {editor.usd_viewer.path}")
    state["undo_before"] = cmds.undoInfo(query=True, undoName=True)
    editor.assign_material_action.trigger()
    wait(lambda: "material_bound" in state["events"] and ball_material(), step_bound, "material bound to /ball")


def step_bound():
    from pxr import UsdShade
    material = ball_material()
    state["material"] = str(material.GetPath())
    surface = material.ComputeSurfaceSource("moonray")[0]
    shader_id = surface.GetIdAttr().Get() if surface else None
    say(f"bound {state['material']}, moonray surface {surface.GetPath() if surface else None} id {shader_id}")
    check(shader_id == "DwaBaseMaterial", "MoonRay surface shader is DwaBaseMaterial")
    undo_name = cmds.undoInfo(query=True, undoName=True)
    check("MoonRay" in undo_name, f"Maya undo entry: {undo_name!r}")
    cmds.undo()
    check(not ball_material(), "Maya Undo removes the binding")
    cmds.redo()
    check(bool(ball_material()), "Maya Redo restores the binding")
    QtCore.QTimer.singleShot(1000, step_sync)


def step_sync():
    editor = state["editor"]
    doc = editor.document
    node = next(n for n in doc.graph.data["nodes"] if n["shader"] == "DwaBaseMaterial")
    state["node"] = node
    state["synced_before"] = state["events"].count("materials_synced")
    editor.set_parameter(node["id"], "roughness", 0.77)
    def roughness():
        from pxr import UsdShade
        surface = ball_material().ComputeSurfaceSource("moonray")[0]
        value = surface.GetInput("roughness").Get() if surface and surface.GetInput("roughness") else None
        state["roughness"] = value
        return value is not None and abs(value - 0.77) < 1e-4
    wait(roughness, step_select, "graph edit synced to the USD shader (roughness 0.77)")


def step_select():
    editor = state["editor"]
    cmds.select(clear=True)
    editor.scene_materials.select_bound(state["material"])
    def selected():
        sel = cmds.ls(selection=True, long=True) or []
        import ufe
        items = [ufe.PathString.string(i.path()) for i in ufe.GlobalSelection.get()]
        state["sel"] = items
        return any(i.endswith(",/ball") for i in items)
    wait(selected, step_open, "Select bound prims selects /ball")


def step_open():
    editor = state["editor"]
    tabs = len(editor.documents)
    # Close the original tab's ownership so the USD material opens fresh.
    editor.shader_graphs.open(state["material"])
    wait(lambda: len(editor.documents) > tabs or editor.document.usd_shader_link
         or editor.material_sync.owner(state["material"]) is not None, step_render,
         "USD material opens in the editor (linked tab or its owner)")


def step_render():
    editor = state["editor"]
    say(f"documents: {[d.graph.data['name'] for d in editor.documents]}, scene library rows: "
        f"{editor.scene_library.topLevelItemCount()}")
    rdla = os.environ.get("HDMOONRAY_RDLA_OUTPUT")
    if not rdla:
        finish()
        return
    panel = next(p for p in cmds.getPanel(type="modelPanel")
                 if cmds.modelEditor(p, query=True, camera=True).split("|")[-1] in ("persp", "perspShape"))
    cmds.setFocus(panel)
    mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "{OVERRIDE}" "{panel}"')
    state["panel"] = panel
    wait(lambda: os.path.exists(rdla) and "DwaBaseMaterial(" in open(rdla).read(), step_done,
         "Hydra Moonray RDL has the editor's DwaBaseMaterial", timeout=60)


def step_done():
    cmds.refresh(currentView=True, force=True, filename=os.path.join(OUT, "view"), fileExtension="png")
    mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "" "{state["panel"]}"')
    QtCore.QTimer.singleShot(2000, finish)


def finish():
    if state.get("finished"):
        return
    state["finished"] = True
    say(f"events: {sorted(set(state['events']))}")
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    say(verdict)
    open(os.path.join(OUT, "editor-usd-result.txt"), "w").write(verdict + "\n")
    QtCore.QTimer.singleShot(500, lambda: cmds.quit(force=True))


QtCore.QTimer.singleShot(int(os.environ.get("M3_SETUP_DELAY_MS", "15000")), setup)
