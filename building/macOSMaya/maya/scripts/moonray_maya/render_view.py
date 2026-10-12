# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Render with Hydra Moonray into Maya's Render View.

mayaHydra registers each Hydra renderer as a Maya renderer, but its render
procedure (hydraRender) only writes an image file: Render > Render Current
Frame renders, yet Render View stays empty. This render procedure renders the
frame into the project's images/tmp folder - where Maya Software puts Render
View frames - and loads it into Render View; while it renders, the
moonrayRenderView plug-in shows MoonRay's progressive images there. Resolution and camera come from
Render View / Render Settings; MoonRay settings from the MoonRay tab.

hydraRender always renders through mayaHydra's USD render settings: by default
/Render/SceneRenderSettings in a stage owned by the UsdDefaultRenderSettings
node, whose BeautyProduct writes ./default.png at 2048x1080 (its -width,
-height and -camera flags do not override that). So each frame points the
products at images/tmp, the requested resolution and camera, and puts them
back afterwards; Maya's own render settings are not touched.
"""
import os
import time

from maya import cmds

from . import RENDERER, render_settings

RENDER_VIEW = "renderView"
DEFAULT_SETTINGS_NODE = "UsdDefaultRenderSettings"
DEFAULT_SETTINGS_PATH = "UsdDefaultRenderSettings,/Render/SceneRenderSettings"
CAMERA_ATTR = "adskUsd:externalCamera"


def tmp_images_dir():
    root = cmds.workspace(query=True, rootDirectory=True)
    images = cmds.workspace(fileRuleEntry="images") or "images"
    path = os.path.join(images if os.path.isabs(images) else os.path.join(root, images), "tmp")
    os.makedirs(path, exist_ok=True)
    return path


def _ensure_render_settings():
    """Select the default render settings, which hydraRender otherwise only
    does on its first render, so the products can be set up before it."""
    plug = DEFAULT_SETTINGS_NODE + ".activeSettingsPath"
    if cmds.objExists(plug) and not cmds.getAttr(plug):
        cmds.setAttr(plug, DEFAULT_SETTINGS_PATH, type="string")


def _render_settings():
    """(products, settings prim) mayaHydra will render, as its helpers find them."""
    from mayaHydra.renderSettings import renderProducts, utils
    return renderProducts.getRenderProductsToApplySettings(), utils.getRenderSettingsPrim()


def _setup_frame(image, width, height, camera_path):
    """Point the products at this frame; return how to put them back.

    Only mayaHydra's USD render settings change, never Maya's render settings:
    Render Settings' Common tab reacts to those (and errors for EXR)."""
    from pxr import Gf, Sdf, UsdRender
    products, settings = _render_settings()
    undo = []

    def set_attr(attr, value):
        undo.append((attr, attr.HasAuthoredValue(), attr.Get()))
        attr.Set(value)

    resolution = Gf.Vec2i(int(width), int(height))
    if settings:
        set_attr(UsdRender.Settings(settings).CreateResolutionAttr(), resolution)
    for product in products:
        set_attr(product.CreateProductNameAttr(), image)
        set_attr(product.CreateResolutionAttr(), resolution)
        set_attr(product.GetPrim().CreateAttribute(CAMERA_ATTR, Sdf.ValueTypeNames.String), camera_path)
    return undo


def _restore(undo):
    for attr, authored, value in reversed(undo):
        if authored:
            attr.Set(value)
        else:
            attr.Clear()


def _begin_progressive(width, height):
    """Show MoonRay's images in Render View while hydraRender runs.

    hydraRender blocks Maya until the frame converges; the moonrayRenderView
    plug-in (maya/plugin) updates Render View from inside that loop through
    hdMoonray's frame hook. Without it the frame appears when it is done."""
    try:
        if not cmds.pluginInfo("moonrayRenderView", query=True, loaded=True):
            cmds.loadPlugin("moonrayRenderView", quiet=True)
        return bool(cmds.moonrayRenderView(begin=True, width=int(width), height=int(height)))
    except Exception as exc:
        cmds.warning(f"Hydra Moonray: no progressive display ({exc})")
        return False


def _camera_transform(camera):
    """Render View passes the camera's transform or shape; products want |transform."""
    node = (cmds.ls(camera or "persp", long=True) or ["|persp"])[0]
    if cmds.nodeType(node) == "camera":
        node = cmds.listRelatives(node, parent=True, fullPath=True)[0]
    return node


def render(width, height, camera, load_final=True):
    """Render procedure: one MoonRay frame into Render View.

    load_final=False leaves Render View showing the last progressive image
    (tests compare it with the final one)."""
    render_settings.ensure_globals()
    camera_path = _camera_transform(camera)
    name = "moonray_" + camera_path.split("|")[-1].replace(":", "_")
    image = os.path.join(tmp_images_dir(), name + ".exr")
    if os.path.exists(image):
        os.remove(image)
    _ensure_render_settings()
    undo = _setup_frame(image, width, height, camera_path)
    progressive = _begin_progressive(width, height)
    started = time.time()
    try:
        cmds.hydraRender(renderer=RENDERER, width=int(width), height=int(height), camera=camera_path)
    finally:
        updates = cmds.moonrayRenderView(finish=True) if progressive else 0
        _restore(undo)
    elapsed = time.time() - started
    if not os.path.exists(image):
        cmds.warning(f"Hydra Moonray: no image was written to {image}")
        return ""
    if load_final and cmds.renderWindowEditor(RENDER_VIEW, exists=True):
        cmds.renderWindowEditor(RENDER_VIEW, edit=True, loadImage=image)
        cmds.renderWindowEditor(RENDER_VIEW, edit=True, pcaption=(
            f"MoonRay  {int(width)}x{int(height)}  {camera_path.split('|')[-1]}  {elapsed:.1f}s"))
    print(f"Hydra Moonray: rendered {image} in {elapsed:.1f}s ({updates} progressive updates)")
    return image
