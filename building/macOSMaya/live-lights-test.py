# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Add lights to a running Hydra Moonray viewport, run inside Maya by
m3-render-test.sh (M3_SCRIPT=live-lights-test.py M3_EXTRA_PLUGINS=mtoa).

Starts Moonray on a cube and ground, then while it renders adds an Arnold
aiSkyDomeLight (mayaHydra hands it over as a dome light with a 1x1 texture,
which used to crash MoonRay's render process) and then a Maya area light.
Checks that the same execComp keeps running, no new execComp crash reports
appear, and each new light reaches the delegate (hdm.log). Writes
$M3_OUT/live-lights.log and $M3_OUT/live-lights-result.txt.
"""
import glob
import os
import subprocess
import time

from maya import cmds, mel
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
HDM_LOG = os.environ.get("HDM_LOG_FILE", os.path.join(OUT, "hdm.log"))
OVERRIDE = "mayaHydraRenderOverride_HdMoonrayRendererPlugin"
REPORTS = os.path.expanduser("~/Library/Logs/DiagnosticReports")

log = open(os.path.join(OUT, "live-lights.log"), "w", buffering=1)
start = time.time()
failures = []
state = {}


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def check(ok, what):
    say(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def exec_comps():
    out = subprocess.run(["pgrep", "-f", "execComp"], capture_output=True, text=True).stdout
    return sorted(out.split())


def crash_reports():
    return set(glob.glob(os.path.join(REPORTS, "execComp-*.ips")))


def synced(name):
    text = open(HDM_LOG).read() if os.path.exists(HDM_LOG) else ""
    return any(name in line for line in text.splitlines() if line.startswith("SyncStart"))


def step(fn, delay):
    def run():
        try:
            fn()
        except Exception:
            import traceback
            say(f"{fn.__name__} ERROR " + traceback.format_exc())
            failures.append(fn.__name__)
            finish()
    QtCore.QTimer.singleShot(delay, run)


def setup():
    cmds.loadPlugin("mayaHydra", quiet=True)
    cube = cmds.polyCube(name="liveCube")[0]
    cmds.setAttr(cube + ".rotateY", 30)
    cmds.polyPlane(name="liveGround", width=8, height=8)
    cmds.move(0, -0.5, 0, "liveGround")
    panel = next(p for p in cmds.getPanel(type="modelPanel")
                 if cmds.modelEditor(p, query=True, camera=True).split("|")[-1] in ("persp", "perspShape"))
    cmds.setFocus(panel)
    cmds.viewFit("persp", all=True)
    state["reports"] = crash_reports()
    mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "{OVERRIDE}" "{panel}"')
    step(started, 6000)


def started():
    state["execcomp"] = exec_comps()
    check(len(state["execcomp"]) == 1, f"Moonray rendering (execComp {state['execcomp']})")
    sky = cmds.shadingNode("aiSkyDomeLight", asLight=True, name="liveSky")
    cmds.setAttr(sky + ".color", 0.4, 0.6, 1.0, type="double3")
    say("added aiSkyDomeLight")
    step(after_sky, 8000)


def after_sky():
    check(synced("liveSky"), "sky dome light reached the delegate")
    check(exec_comps() == state["execcomp"], f"same execComp still running after the sky dome ({exec_comps()})")
    area = cmds.shadingNode("areaLight", asLight=True, name="liveArea")
    if cmds.nodeType(area) == "areaLight":
        area = cmds.listRelatives(area, parent=True)[0]
    cmds.xform(area, translation=(0, 3, 2), rotation=(-60, 0, 0), scale=(2, 2, 2))
    say("added area light")
    step(after_area, 8000)


def after_area():
    check(synced("liveArea"), "area light reached the delegate")
    check(exec_comps() == state["execcomp"], f"same execComp still running after the area light ({exec_comps()})")
    new = crash_reports() - state["reports"]
    check(not new, f"no new execComp crash reports {sorted(os.path.basename(p) for p in new)}")
    finish()


def finish():
    if state.get("done"):
        return
    state["done"] = True
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    say(verdict)
    open(os.path.join(OUT, "live-lights-result.txt"), "w").write(verdict + "\n")
    QtCore.QTimer.singleShot(1000, lambda: cmds.quit(force=True))


step(setup, int(os.environ.get("M3_SETUP_DELAY_MS", "15000")))
