# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Memory while orbiting a Hydra viewport, run inside Maya by m3-render-test.sh
(M3_SCRIPT=orbit-memory-test.py).

Switches the persp panel to ORBIT_OVERRIDE (default Hydra Moonray), orbits the
camera every ORBIT_STEP_MS for ORBIT_SECONDS and samples Maya's RSS (and
execComp's, if running) every SAMPLE_MS into $M3_OUT/orbit-memory.csv. Writes a
summary (start, peak, end, growth over the last half) to orbit-memory.log.
"""
import os
import subprocess
import time

from maya import cmds, mel
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
OVERRIDE = os.environ.get("ORBIT_OVERRIDE", "mayaHydraRenderOverride_HdMoonrayRendererPlugin")
SECONDS = float(os.environ.get("ORBIT_SECONDS", "90"))
STEP_MS = int(os.environ.get("ORBIT_STEP_MS", "100"))
SAMPLE_MS = int(os.environ.get("ORBIT_SAMPLE_MS", "2000"))

log = open(os.path.join(OUT, "orbit-memory.log"), "w", buffering=1)
csv = open(os.path.join(OUT, "orbit-memory.csv"), "w", buffering=1)
csv.write("seconds,maya_mb,execcomp_mb\n")
state = {"samples": []}
start = time.time()


def rss_mb(pid):
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) // 1024 if out else 0


def execcomp_mb():
    pids = subprocess.run(["pgrep", "-f", "execComp"], capture_output=True, text=True).stdout.split()
    return sum(rss_mb(p) for p in pids)


def sample():
    t = time.time() - start
    row = (t, rss_mb(os.getpid()), execcomp_mb())
    state["samples"].append(row)
    csv.write("%.1f,%d,%d\n" % row)


def setup():
    cmds.loadPlugin("mayaHydra", quiet=True)
    cube = cmds.polyCube(name="orbitCube")[0]
    cmds.setAttr(cube + ".rotateY", 30)
    cmds.polyPlane(name="orbitGround", width=8, height=8)
    cmds.move(0, -0.5, 0, "orbitGround")
    cmds.polySphere(name="orbitBall")
    cmds.move(2, 0.5, 0, "orbitBall")
    panel = next(p for p in cmds.getPanel(type="modelPanel")
                 if cmds.modelEditor(p, query=True, camera=True).split("|")[-1] in ("persp", "perspShape"))
    cmds.setFocus(panel)
    cmds.viewFit("persp", all=True)
    if OVERRIDE:
        mel.eval(f'setRendererAndOverrideInModelPanel $gViewport2 "{OVERRIDE}" "{panel}"')
    log.write(f"override {OVERRIDE or 'Viewport 2.0'}; orbit {SECONDS}s every {STEP_MS}ms\n")
    QtCore.QTimer.singleShot(5000, begin)


def begin():
    state["end"] = time.time() + SECONDS
    sample()
    state["orbit"] = QtCore.QTimer()
    state["orbit"].timeout.connect(orbit)
    state["orbit"].start(STEP_MS)
    state["sampler"] = QtCore.QTimer()
    state["sampler"].timeout.connect(sample)
    state["sampler"].start(SAMPLE_MS)


def orbit():
    if time.time() > state["end"]:
        state["orbit"].stop()
        state["sampler"].stop()
        sample()
        finish()
        return
    cmds.orbit("persp", rotationAngles=(0, 3))


def finish():
    s = state["samples"]
    maya = [m for _, m, _ in s]
    half = s[len(s) // 2]
    log.write(f"maya MB: start {maya[0]}  peak {max(maya)}  end {maya[-1]}  "
              f"growth {maya[-1] - maya[0]}  growth over last half {maya[-1] - half[1]}\n")
    ec = [e for _, _, e in s if e]
    if ec:
        log.write(f"execComp MB: start {ec[0]}  peak {max(ec)}  end {ec[-1]}\n")
    QtCore.QTimer.singleShot(1000, lambda: cmds.quit(force=True))


QtCore.QTimer.singleShot(int(os.environ.get("M3_SETUP_DELAY_MS", "15000")), setup)
