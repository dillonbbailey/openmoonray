# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Why doesn't a material show up? Run in Maya's Script Editor (Python):

    from moonray_maya import diagnose; diagnose.materials()

For the USD prims selected in Maya (or every stage if none are), prints the
viewport renderer, each stage's edit target, and for every piece of geometry
under the selection the material it resolves to and where that binding comes
from. A direct binding on a mesh wins over one on its parent Xform.
"""
from maya import cmds


def _viewport():
    panel = cmds.getPanel(withFocus=True)
    if cmds.getPanel(typeOf=panel) != "modelPanel":
        panel = next(iter(cmds.getPanel(visiblePanels=True) or []), panel)
    try:
        override = cmds.modelEditor(panel, query=True, rendererOverrideName=True)
        renderer = cmds.modelEditor(panel, query=True, rendererName=True)
        return f"{panel}: {override or renderer}"
    except Exception:
        return f"{panel}: (not a model panel)"


def _binding_source(prim, material):
    """The prim whose binding the resolved material comes from."""
    from pxr import UsdShade
    walk = prim
    while walk and not walk.IsPseudoRoot():
        api = UsdShade.MaterialBindingAPI(walk)
        for purpose in ("", "full", "preview"):
            direct = api.GetDirectBinding(purpose)
            if direct.GetMaterialPath() == material:
                return f"direct binding on {walk.GetPath()}" + (f" ({purpose})" if purpose else "")
        walk = walk.GetParent()
    return "collection binding"


def _report(proxy, stage, roots):
    from pxr import Usd, UsdGeom, UsdShade
    print(f"\nStage {proxy}")
    layer = stage.GetEditTarget().GetLayer()
    print(f"  edit target: {layer.identifier}" + ("" if layer.permissionToEdit else "  (NOT EDITABLE)"))
    for root in roots:
        prim = stage.GetPrimAtPath(root)
        if not prim:
            print(f"  {root}: not in the stage")
            continue
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        print(f"  {root} [{prim.GetTypeName()}] -> {material.GetPath() if material else 'no material'}")
        for child in Usd.PrimRange(prim):
            if child == prim or not child.IsA(UsdGeom.Gprim):
                continue
            bound, _ = UsdShade.MaterialBindingAPI(child).ComputeBoundMaterial()
            if not bound:
                print(f"    {child.GetPath()}: no material (MoonRay default)")
                continue
            source = _binding_source(child, bound.GetPath())
            surface = bound.ComputeSurfaceSource("moonray")[0]
            kind = surface.GetIdAttr().Get() if surface else None
            print(f"    {child.GetPath()}: {bound.GetPath()}  [{source}]"
                  + (f"  moonray surface {kind}" if kind else "  (no MoonRay surface: default material)"))


def materials():
    import mayaUsd.ufe
    print("Viewport renderer: " + _viewport())
    selected = {}
    for item in cmds.ls(selection=True, long=True) or []:
        if "," in item:
            proxy, path = item.split(",", 1)
            selected.setdefault(proxy, []).append(path)
    if not selected:
        for proxy in cmds.ls(type="mayaUsdProxyShape", long=True) or []:
            selected[proxy] = ["/"]
        if not selected:
            print("No USD stages in the scene and no USD prims selected: "
                  "the Material Editor only assigns to prims in a mayaUsd stage.")
            return
        print("Nothing in a USD stage is selected; reporting whole stages.")
    for proxy, roots in selected.items():
        stage = mayaUsd.ufe.getStage(proxy)
        if stage:
            _report(proxy, stage, roots)
