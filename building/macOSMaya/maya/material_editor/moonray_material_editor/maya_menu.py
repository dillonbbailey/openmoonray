"""The MoonRay menu in Maya's main menu bar."""
from maya import cmds, mel

MENU = "moonrayMenu"


def install():
    main = mel.eval("$moonrayMainWindow = $gMainWindow")
    if cmds.menu(MENU, exists=True):
        cmds.deleteUI(MENU)
    cmds.menu(MENU, label="MoonRay", parent=main, tearOff=True)
    cmds.menuItem(label="Material Editor", annotation="Open the MoonRay Material Editor",
                  command=lambda *_: _show())


def _show():
    from . import maya_host
    maya_host.show()
