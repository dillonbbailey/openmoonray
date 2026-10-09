"""Host the Material Editor in Maya: a dockable workspace control and job launching.

One editor per Maya session. It lives in a retained workspace control, so
closing the panel only hides it - open graphs, undo history and running
previews stay, as with MoonLab's floating Material Editor window. The control's
uiScript re-creates the contents when Maya restores a workspace layout.

    import moonray_material_editor.maya_host as host
    host.show()
"""
import os
from pathlib import Path

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .model import EDITOR_ROOT

CONTROL = "moonrayMaterialEditorWorkspaceControl"
LABEL = "MoonRay Material Editor"
UI_SCRIPT = "import moonray_material_editor.maya_host as host; host.build_into_current_control()"

_editor = None
_quit_job = None


def _maya():
    try:
        from maya import cmds
    except ImportError:
        return None
    # Outside Maya's UI (tests, plain mayapy) maya.cmds is empty: no host.
    return cmds if hasattr(cmds, "workspaceControl") else None


def editor():
    """The session's editor, created on first use."""
    global _editor, _quit_job
    if _editor is None:
        from . import ui_icons_rc  # noqa: F401 - registers :/lunatic/ resources
        from .editor import MaterialEditor
        from .model import Catalog
        _editor = MaterialEditor(Catalog())
        cmds = _maya()
        if cmds is not None and _quit_job is None:
            _quit_job = cmds.scriptJob(event=["quitApplication", shutdown], protected=True)
    return _editor


def build_into_current_control():
    """uiScript of the workspace control: put the editor into it."""
    from maya import OpenMayaUI
    _attach(OpenMayaUI.MQtUtil.getCurrentParent())


def _attach(pointer):
    from shiboken6 import wrapInstance
    parent = wrapInstance(int(pointer), QWidget)
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    window = editor()
    window.setParent(parent)
    layout.addWidget(window)
    window.show()


def show():
    """Open (or raise) the Material Editor panel."""
    cmds = _maya()
    if cmds.workspaceControl(CONTROL, exists=True):
        cmds.workspaceControl(CONTROL, edit=True, visible=True, restore=True)
        if editor().parent() is None:
            # The control exists (e.g. from a saved layout) but holds no editor.
            from maya import OpenMayaUI
            _attach(OpenMayaUI.MQtUtil.findControl(CONTROL))
    else:
        cmds.workspaceControl(CONTROL, label=LABEL, retain=True, floating=True,
                              initialWidth=1500, initialHeight=920, minimumWidth=1000,
                              uiScript=UI_SCRIPT)
    update_title(editor())
    editor().canvas.setFocus()
    return editor()


def raise_editor(window):
    cmds = _maya()
    if cmds is not None and cmds.workspaceControl(CONTROL, exists=True):
        cmds.workspaceControl(CONTROL, edit=True, visible=True, restore=True)


def update_title(window):
    """MoonLab sets the floating window's title; here it labels the panel."""
    cmds = _maya()
    if cmds is not None and cmds.workspaceControl(CONTROL, exists=True):
        cmds.workspaceControl(CONTROL, edit=True, label=window.windowTitle())


def shutdown():
    """Maya is quitting: stop renders and background jobs."""
    global _editor
    if _editor is not None:
        _editor.shutdown()
        _editor = None


def job_command(module):
    """Command line for a background job module (worker, render_view_worker).

    Jobs run in the workspace's MoonRay environment, not Maya's (run-job.sh).
    """
    keep = {name: os.environ[name] for name in ("HOME", "USER", "LOGNAME", "TMPDIR") if name in os.environ}
    return (["/usr/bin/env", "-i", *(f"{k}={v}" for k, v in keep.items()),
             "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
             "/bin/bash", str(Path(EDITOR_ROOT) / "run-job.sh"), module])
