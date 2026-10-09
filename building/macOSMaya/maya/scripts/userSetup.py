# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""MoonRay for Maya: add the MoonRay menu once Maya's UI is up."""
from maya import cmds, utils


def _moonray_menu():
    if cmds.about(batch=True):
        return
    from moonray_material_editor import maya_menu
    maya_menu.install()


utils.executeDeferred(_moonray_menu)
