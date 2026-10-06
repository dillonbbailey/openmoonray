# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""M5 stability test, run inside Maya by m3-render-test.sh (M3_SCRIPT=m5-stability-test.py).

Phases, on Qt timers so Maya's event loop keeps running:
  1. switch:   Viewport 2.0 <-> Hydra Moonray, M5_SWITCHES times. After each
               switch, check execComp is running (Moonray) or gone (VP2).
  2. navigate: with Moonray active, orbit the persp camera every
               M5_NAV_STEP_MS for M5_NAV_SECONDS; the session must survive.
  3. quit:     quit Maya while Moonray is still rendering. m3-render-test.sh
               then records any execComp that outlived Maya (orphans.txt).
Every check logs Maya's RSS, open file descriptors and leftover
/tmp/exec-* files, to spot leaks across switches. Writes $M3_OUT/m5.log and
a one-line verdict to $M3_OUT/m5-result.txt.
"""
import glob
import os
import subprocess
import time

from maya import cmds, mel, utils
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
OVERRIDE = "mayaHydraRenderOverride_HdMoonrayRendererPlugin"
SWITCHES = int(os.environ.get("M5_SWITCHES", "20"))
ON_MS = int(os.environ.get("M5_ON_MS", "4000"))
OFF_MS = int(os.environ.get("M5_OFF_MS", "2000"))
NAV_SECONDS = float(os.environ.get("M5_NAV_SECONDS", "10"))
NAV_STEP_MS = int(os.environ.get("M5_NAV_STEP_MS", "100"))
SETUP_DELAY_MS = int(os.environ.get("M3_SETUP_DELAY_MS", "30000"))

os.makedirs(OUT, exist_ok=True)
log = open(os.path.join(OUT, "m5.log"), "w", buffering=1)
start = time.time()
state = {"panel": None, "switch": 0, "failures": [], "nav_steps": 0, "nav_end": 0.0}


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def fail(msg):
    state["failures"].append(msg)
    say("FAIL " + msg)


def exec_comps():
    ps = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return [l.strip() for l in ps.splitlines() if "execComp" in l and "grep" not in l]


def vitals():
    rss = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout.strip()
    fds = len(os.listdir("/dev/fd"))
    tmp = len(glob.glob("/tmp/exec-*"))
    return f"rss={int(rss) // 1024}MB fds={fds} tmp_exec={tmp}"


def set_renderer(override):
    mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "{override}" "{state["panel"]}"')


def setup():
    cmds.scriptEditorInfo(historyFilename=os.path.join(OUT, "script-editor.txt"), writeHistory=True)
    for plugin in os.environ.get("M3_EXTRA_PLUGINS", "").split() + ["mayaUsdPlugin", "mayaHydra"]:
        cmds.loadPlugin(plugin, quiet=True)
    cmds.file(new=True, force=True)
    cube = cmds.polyCube(width=2, height=2, depth=2, name="m5Cube")[0]
    cmds.setAttr(cube + ".rotateY", 30)
    panel = next(p for p in cmds.getPanel(type="modelPanel")
                 if cmds.modelEditor(p, query=True, camera=True).split("|")[-1] in ("persp", "perspShape"))
    state["panel"] = panel
    cmds.setFocus(panel)
    cmds.viewFit("persp", all=True)
    say(f"setup: panel {panel}; {vitals()}")
    # MtoA defers ActivateViewport20 after File > New; let the deferred queue drain.
    cmds.evalDeferred(lambda: QtCore.QTimer.singleShot(2000, switch_on), lowestPriority=True)


def switch_on():
    state["switch"] += 1
    set_renderer(OVERRIDE)
    QtCore.QTimer.singleShot(ON_MS, check_on)


def check_on():
    n = state["switch"]
    procs = exec_comps()
    ov = cmds.modelEditor(state["panel"], query=True, rendererOverrideName=True)
    say(f"switch {n:2d} ON : override={'moonray' if ov == OVERRIDE else repr(ov)} execComp={len(procs)} {vitals()}")
    if ov != OVERRIDE:
        fail(f"switch {n}: override is {ov!r}, not Moonray")
    if len(procs) != 1:
        fail(f"switch {n}: {len(procs)} execComp while Moonray active, want 1")
    if n < SWITCHES:
        set_renderer("")
        QtCore.QTimer.singleShot(OFF_MS, check_off)
    else:
        say(f"navigate: orbit every {NAV_STEP_MS}ms for {NAV_SECONDS}s")
        state["nav_end"] = time.time() + NAV_SECONDS
        state["nav_pid"] = procs[0].split()[0] if procs else None
        QtCore.QTimer.singleShot(NAV_STEP_MS, navigate)


def check_off():
    n = state["switch"]
    procs = exec_comps()
    say(f"switch {n:2d} OFF: execComp={len(procs)} {vitals()}")
    if procs:
        fail(f"switch {n}: {len(procs)} execComp still running {OFF_MS}ms after switching to VP2")
    switch_on()


def navigate():
    if time.time() < state["nav_end"]:
        cmds.setAttr("persp.rotateY", cmds.getAttr("persp.rotateY") + 2)
        cmds.setAttr("persp.translateY", cmds.getAttr("persp.translateY") + 0.01)
        cmds.refresh()
        state["nav_steps"] += 1
        QtCore.QTimer.singleShot(NAV_STEP_MS, navigate)
        return
    procs = exec_comps()
    same = bool(procs) and procs[0].split()[0] == state.get("nav_pid")
    say(f"navigate done: {state['nav_steps']} steps, execComp={len(procs)} "
        f"(same process: {same}) {vitals()}")
    if len(procs) != 1:
        fail(f"navigate: {len(procs)} execComp after navigation, want 1")
    # Give the last camera update time to render, then quit with Moonray active.
    QtCore.QTimer.singleShot(3000, quit_active)


def quit_active():
    verdict = "PASS" if not state["failures"] else f"FAIL ({len(state['failures'])})"
    say(f"quit while Moonray active; result {verdict}; {vitals()}")
    with open(os.path.join(OUT, "m5-result.txt"), "w") as f:
        f.write(verdict + "\n" + "\n".join(state["failures"]) + "\n")
    log.close()
    cmds.quit(force=True)


utils.executeDeferred(lambda: QtCore.QTimer.singleShot(SETUP_DELAY_MS, setup))
