# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""MoonRay for Maya: the MoonRay menu, Render View rendering and render settings UI."""
from maya import cmds, utils


def _moonray_menu():
    if cmds.about(batch=True):
        return
    from moonray_material_editor import maya_menu
    maya_menu.install()
    # Render View rendering and the MoonRay render settings UI for Hydra Moonray.
    from moonray_maya import setup
    setup.install()


utils.executeDeferred(_moonray_menu)
