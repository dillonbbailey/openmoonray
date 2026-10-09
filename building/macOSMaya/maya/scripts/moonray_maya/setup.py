# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Hook MoonRay's Render View rendering and settings UI into mayaHydra.

mayaHydra (re)creates the "HdMoonrayRendererPlugin" Maya renderer and its
generated MEL procedures when it loads, so this runs after every load of the
plugin (and once now, if it is already loaded).
"""
from maya import OpenMaya, cmds, mel

from . import RENDERER

_callback = None

MEL = r'''
global proc string moonrayRenderViewRender(int $width, int $height, int $doShadows, int $doGlowPass, string $camera, string $option)
{
    return python("import moonray_maya.render_view as rv; rv.render(" + $width + ", " + $height + ", '" + $camera + "')");
}
global proc moonrayRenderSettingsCreateTab()
{
    python("import moonray_maya.render_settings as rs; rs.create_tab()");
}
global proc moonrayRenderSettingsUpdateTab()
{
    python("import moonray_maya.render_settings as rs; rs.update_tab()");
}
// Replaces mayaHydra's generated option box / Attribute Editor contents.
global proc mayaHydraRenderOverride_HdMoonrayRendererPluginOptions(int $fromAE)
{
    python("import moonray_maya.render_settings as rs; rs.build_options(" + $fromAE + ")");
}
'''


def hook():
    """Point mayaHydra's Hydra Moonray renderer at our procedures."""
    if RENDERER not in (cmds.renderer(query=True, namesOfAvailableRenderers=True) or []):
        return
    mel.eval(MEL)
    cmds.renderer(RENDERER, edit=True, renderProcedure="moonrayRenderViewRender")
    tabs = cmds.renderer(RENDERER, query=True, globalsTabLabels=True) or []
    if "MoonRay" not in tabs:
        cmds.renderer(RENDERER, edit=True, addGlobalsTab=(
            "MoonRay", "moonrayRenderSettingsCreateTab", "moonrayRenderSettingsUpdateTab"))


def _after_plugin_load(strings, client_data=None):
    if strings and "mayaHydra" in strings[-1]:
        cmds.evalDeferred(hook)


def install():
    global _callback
    if cmds.about(batch=True):
        return
    if _callback is None:
        _callback = OpenMaya.MSceneMessage.addStringArrayCallback(
            OpenMaya.MSceneMessage.kAfterPluginLoad, _after_plugin_load)
    if cmds.pluginInfo("mayaHydra", query=True, loaded=True):
        hook()
