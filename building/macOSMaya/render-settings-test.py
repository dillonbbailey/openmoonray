# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Moonray render settings in mayaHydra, run inside Maya by m3-render-test.sh
(M3_SCRIPT=render-settings-test.py, with HDMOONRAY_RDLA_OUTPUT set).

  1. mayaHydra made render globals for MoonRay's scene variables
  2. the option box (viewport Renderer menu) shows them -> optionbox.png
  3. set pixel samples / max depth as the option box does
     (mtohRenderOverride_ApplySetting), switch the viewport to Hydra Moonray
  4. the RDL MoonRay received has those SceneVariables values
Writes $M3_OUT/settings.log and $M3_OUT/settings-result.txt.
"""
import os
import re
import time

from maya import cmds, mel
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
RDLA = os.environ["HDMOONRAY_RDLA_OUTPUT"]
RENDERER = "HdMoonrayRendererPlugin"
OVERRIDE = "mayaHydraRenderOverride_" + RENDERER
VALUES = {"pixel_samples": 2, "max_depth": 7, "max_diffuse_depth": 3}


def key(name):
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)

log = open(os.path.join(OUT, "settings.log"), "w", buffering=1)
start = time.time()
failures = []


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def check(ok, what):
    say(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def setup():
    try:
        cmds.loadPlugin("mayaHydra", quiet=True)
        cube = cmds.polyCube(name="settingsCube")[0]
        cmds.setAttr(cube + ".rotateY", 30)
        mel.eval(f'{OVERRIDE}OptionBox()')
        window = OVERRIDE + "OptionsWindow"
        check(cmds.window(window, exists=True), "option box window opened")
        names = ["sampling_mode", "pixel_samples", "min_adaptive_samples", "max_adaptive_samples",
                 "target_adaptive_error", "sample_clamping_depth", "sample_clamping_value", "max_depth",
                 "max_diffuse_depth", "max_glossy_depth", "max_mirror_depth", "max_hair_depth",
                 "max_presence_depth", "max_volume_depth", "texture_cache_size", "enable_presence_shadows"]
        attrs = [a for a in names if cmds.attributeQuery(f"{RENDERER}__{key(a)}", node="defaultRenderGlobals", exists=True)]
        check(len(attrs) == 16, f"{len(attrs)} scene variable render globals")
        from maya import OpenMayaUI
        from shiboken6 import wrapInstance
        from PySide6.QtWidgets import QWidget
        widget = wrapInstance(int(OpenMayaUI.MQtUtil.findWindow(window)), QWidget)
        widget.resize(620, 900)
        QtCore.QTimer.singleShot(1000, lambda: (widget.grab().save(os.path.join(OUT, "optionbox.png")), apply_values()))
    except Exception:
        import traceback
        say("SETUP ERROR " + traceback.format_exc())
        failures.append("setup")
        finish()


def apply_values():
    for name, value in VALUES.items():
        attr = f"{RENDERER}__{key(name)}"
        cmds.setAttr("defaultRenderGlobals." + attr, value)
        # What the option box's change command runs.
        mel.eval(f"mtohRenderOverride_ApplySetting {RENDERER} {attr} defaultRenderGlobals")
    panel = next(p for p in cmds.getPanel(type="modelPanel")
                 if cmds.modelEditor(p, query=True, camera=True).split("|")[-1] in ("persp", "perspShape"))
    cmds.setFocus(panel)
    cmds.viewFit("persp", all=True)
    mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "{OVERRIDE}" "{panel}"')
    deadline = time.time() + 60

    def poll():
        if os.path.exists(RDLA) and "SceneVariables" in open(RDLA).read():
            QtCore.QTimer.singleShot(1000, verify)
        elif time.time() > deadline:
            check(False, "RDL written by Hydra Moonray")
            finish()
        else:
            QtCore.QTimer.singleShot(500, poll)
    poll()


def verify():
    text = open(RDLA).read()
    block = re.search(r"SceneVariables \{(.*?)\n\}", text, re.S)
    body = block.group(1) if block else ""
    for name, value in VALUES.items():
        found = re.search(r'\["' + name + r'"\] = ([^,]+),', body)
        got = found.group(1).strip() if found else None
        check(got == str(value), f"SceneVariables {name} = {got} (want {value})")
    finish()


def finish():
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    say(verdict)
    open(os.path.join(OUT, "settings-result.txt"), "w").write(verdict + "\n")
    QtCore.QTimer.singleShot(1000, lambda: cmds.quit(force=True))


QtCore.QTimer.singleShot(int(os.environ.get("M3_SETUP_DELAY_MS", "15000")), setup)
