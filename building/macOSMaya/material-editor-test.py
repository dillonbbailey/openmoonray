# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Material Editor test, run inside Maya by m3-render-test.sh
(M3_SCRIPT=material-editor-test.py).

Checks the MoonRay menu (userSetup.py), opens the editor in its workspace
control, waits for the automatic preview render, then:
  - grabs the editor to $M3_OUT/editor.png and copies the preview image
  - checks Maya's application stylesheet / palette were left alone
  - checks menu shortcuts are scoped to the editor (not Maya's window)
  - closes the panel (editor must survive: retain) and re-opens it
Writes $M3_OUT/editor.log and a verdict to $M3_OUT/editor-result.txt.
"""
import os
import shutil
import time

from maya import cmds
from PySide6 import QtCore
from PySide6.QtWidgets import QApplication

OUT = os.environ["M3_OUT"]
RENDER_TIMEOUT_S = float(os.environ.get("EDITOR_RENDER_TIMEOUT", "120"))

log = open(os.path.join(OUT, "editor.log"), "w", buffering=1)
start = time.time()
failures = []
state = {}


def say(msg):
    log.write(f"[{time.time() - start:6.1f}s] {msg}\n")


def check(ok, what):
    say(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def setup():
    try:
        app = QApplication.instance()
        state["app_style"] = app.styleSheet()
        state["app_palette"] = app.palette().color(app.palette().ColorRole.Window).name()
        check(cmds.menu("moonrayMenu", exists=True), "MoonRay menu installed by userSetup.py")
        items = cmds.menu("moonrayMenu", query=True, itemArray=True) or []
        labels = [cmds.menuItem(i, query=True, label=True) for i in items]
        check("Material Editor" in labels, f"menu items {labels}")
        from moonray_material_editor import maya_host
        state["host"] = maya_host
        editor = maya_host.show()
        state["editor"] = editor
        check(cmds.workspaceControl(maya_host.CONTROL, exists=True), "workspace control created")
        check(editor.isVisible(), "editor visible")
        state["deadline"] = time.time() + RENDER_TIMEOUT_S
        QtCore.QTimer.singleShot(1000, wait_render)
    except Exception as e:
        import traceback
        say("SETUP ERROR: " + traceback.format_exc())
        failures.append("setup")
        finish()


def wait_render():
    editor = state["editor"]
    doc = editor.document
    busy = editor.process is not None
    if (busy or doc.image.isNull()) and time.time() < state["deadline"]:
        QtCore.QTimer.singleShot(1000, wait_render)
        return
    say(f"preview status: {editor.preview_status.text()!r}")
    check(not doc.image.isNull(), "preview rendered")
    if doc.preview_dir is not None:
        src = os.path.join(doc.preview_dir.name, "preview.png")
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(OUT, "preview.png"))
    log_text = editor.log.toPlainText()
    open(os.path.join(OUT, "render-log.txt"), "w").write(log_text)
    QtCore.QTimer.singleShot(500, inspect)


def inspect():
    editor, host = state["editor"], state["host"]
    app = QApplication.instance()
    editor.window().grab().save(os.path.join(OUT, "editor-window.png"))
    editor.grab().save(os.path.join(OUT, "editor.png"))
    check(app.styleSheet() == state["app_style"], "Maya application stylesheet unchanged")
    check(app.palette().color(app.palette().ColorRole.Window).name() == state["app_palette"], "Maya palette unchanged")
    scoped = [a.text() for a in editor.actions() if a.shortcut().toString()
              and a.shortcutContext() != QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut]
    check(not scoped, f"shortcuts scoped to the editor (unscoped: {scoped})")
    shortcuts = {a.text(): a.shortcut().toString() for a in editor.actions() if a.shortcut().toString()}
    say(f"editor shortcuts: {shortcuts}")
    say(f"workspace control floating={cmds.workspaceControl(host.CONTROL, query=True, floating=True)} "
        f"label={cmds.workspaceControl(host.CONTROL, query=True, label=True)!r}")
    # Close the panel: retained, so the same editor (and its documents) survive.
    cmds.workspaceControl(host.CONTROL, edit=True, close=True)
    QtCore.QTimer.singleShot(1000, reopen)


def reopen():
    host = state["host"]
    editor = host.show()
    check(editor is state["editor"], "same editor after close + reopen")
    check(editor.isVisible(), "editor visible after reopen")
    check(len(editor.documents) >= 1 and not editor.document.image.isNull(), "documents and preview kept")
    QtCore.QTimer.singleShot(1000, finish)


def finish():
    verdict = "PASS" if not failures else "FAIL: " + "; ".join(failures)
    say(verdict)
    open(os.path.join(OUT, "editor-result.txt"), "w").write(verdict + "\n")
    QtCore.QTimer.singleShot(500, lambda: cmds.quit(force=True))


# userSetup.py adds the menu with executeDeferred; give Maya's UI time to settle.
QtCore.QTimer.singleShot(int(os.environ.get("M3_SETUP_DELAY_MS", "15000")), setup)
