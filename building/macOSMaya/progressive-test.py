# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Progressive Render View display, run inside Maya by m3-render-test.sh
(M3_SCRIPT=progressive-test.py).

Renders a frame heavy enough to take several seconds (pixel samples 16) with
Hydra Moonray into Render View, without loading the final image, so Render View
keeps the last image the moonrayRenderView plug-in pushed. Saves it
(progressive.png) next to the final frame (the EXR in images/tmp), and checks
that updates arrived during the render. Writes $M3_OUT/progressive.log and
$M3_OUT/progressive-result.txt.
"""
import os
import time

from maya import cmds
from PySide6 import QtCore

OUT = os.environ["M3_OUT"]
RENDERER = "HdMoonrayRendererPlugin"
PROJECT = os.path.join(OUT, "project")
log = open(os.path.join(OUT, "progressive.log"), "w", buffering=1)
start = time.time()
failures = []


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def check(ok, what):
    say(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def run():
    try:
        cmds.scriptEditorInfo(historyFilename=os.path.join(OUT, "script-editor.txt"), writeHistory=True)
        os.makedirs(PROJECT, exist_ok=True)
        cmds.workspace(PROJECT, newWorkspace=True)
        cmds.workspace(fileRule=["images", "images"])
        cmds.workspace(saveWorkspace=True)
        cmds.workspace(PROJECT, openWorkspace=True)
        cmds.loadPlugin("mayaHydra", quiet=True)
        cube = cmds.polyCube(name="pCube")[0]
        cmds.setAttr(cube + ".rotateY", 30)
        cmds.polyPlane(name="pGround", width=8, height=8)
        cmds.move(0, -0.5, 0, "pGround")
        # Something asymmetric, to show a flipped image.
        cmds.polySphere(name="pBall", radius=0.4)
        cmds.move(1.5, 1.2, 0, "pBall")
        light = cmds.directionalLight(name="pSun", intensity=2)
        cmds.xform(cmds.listRelatives(light, parent=True)[0], rotation=(-50, 30, 0))
        cmds.setAttr("persp.translate", 4, 3, 5)
        cmds.setAttr("persp.rotate", -25, 38, 0)
        cmds.setAttr("defaultRenderGlobals.currentRenderer", RENDERER, type="string")
        from moonray_maya import render_settings, render_view
        render_settings.ensure_globals()
        cmds.setAttr(render_settings.plug("pixelSamples"), 16)
        cmds.RenderViewWindow()
        t = time.time()
        image = render_view.render(640, 360, "persp", load_final=False)
        elapsed = time.time() - t
        say(f"render took {elapsed:.1f}s -> {image}")
        check(bool(image) and os.path.exists(image), "final frame written")
        cmds.setAttr("defaultRenderGlobals.imageFormat", 32)
        progressive = os.path.join(OUT, "progressive.png")
        cmds.renderWindowEditor("renderView", edit=True, writeImage=progressive)
        check(os.path.exists(progressive) and os.path.getsize(progressive) > 5000,
              "Render View shows a progressive image (progressive.png)")
        hist = open(os.path.join(OUT, "script-editor.txt")).read() if os.path.exists(os.path.join(OUT, "script-editor.txt")) else ""
        import re
        m = re.findall(r"\((\d+) progressive updates\)", hist)
        updates = int(m[-1]) if m else -1
        say(f"progressive updates: {updates}")
        check(updates >= 2, "several progressive updates during the render")
        if image:
            import shutil
            shutil.copy(image, os.path.join(OUT, "final.exr"))
            # The final frame as Render View shows it (loaded like a normal render
            # does), saved the same way, to compare with the last progressive image.
            cmds.renderWindowEditor("renderView", edit=True, loadImage=image)
            final_rv = os.path.join(OUT, "final-renderview.png")
            cmds.renderWindowEditor("renderView", edit=True, writeImage=final_rv)
            from PySide6.QtGui import QImage
            a, b = QImage(progressive), QImage(final_rv)
            def mean(img):
                img = img.convertToFormat(QImage.Format_RGB32)
                total = 0
                for y in range(0, img.height(), 4):
                    for x in range(0, img.width(), 4):
                        c = img.pixel(x, y)
                        total += ((c >> 16) & 255) + ((c >> 8) & 255) + (c & 255)
                return total / (3 * (img.width() // 4) * (img.height() // 4))
            ma, mb = mean(a), mean(b)
            say(f"Render View mean brightness: progressive {ma:.1f}, final {mb:.1f}")
            check(abs(ma - mb) <= 0.05 * mb, "progressive and final look the same in Render View (within 5%)")
    except Exception:
        import traceback
        say("ERROR " + traceback.format_exc())
        failures.append("exception")
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    say(verdict)
    open(os.path.join(OUT, "progressive-result.txt"), "w").write(verdict + "\n")
    QtCore.QTimer.singleShot(1000, lambda: cmds.quit(force=True))


QtCore.QTimer.singleShot(int(os.environ.get("M3_SETUP_DELAY_MS", "15000")), run)
