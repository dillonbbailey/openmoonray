"""Copy shader-graph material snapshots into a USD stage and bind them."""
from functools import lru_cache
import os
from pathlib import Path

from pxr import Sdf, UsdGeom, UsdLux, UsdShade

from .model import Catalog, Graph, SHADER_TYPES, TERMINALS, PROJECTION_CAMERAS
from .worker import create_stage


@lru_cache(maxsize=1)
def material_catalog():
    return Catalog()


def can_bind_material(prim):
    if not prim or prim.IsPseudoRoot() or not prim.IsActive() or prim.IsInstanceProxy() or prim.IsInPrototype():
        return False
    if prim.IsA(UsdGeom.Subset):
        return prim.GetParent().IsA(UsdGeom.Mesh) and UsdGeom.Subset(prim).GetElementTypeAttr().Get() == UsdGeom.Tokens.face
    return bool(UsdGeom.Imageable(prim) and not prim.IsA(UsdGeom.Camera) and
                (not prim.HasAPI(UsdLux.LightAPI) or prim.IsA(UsdGeom.Gprim)))


def material_graph(data, base_dir):
    graph = Graph(material_catalog(), data)
    if not graph.data["surface"] and not graph.data["volume"]:
        raise ValueError("Connect a surface or volume output in the shader graph before binding a material.")
    # Scene setup and unrelated nodes do not belong to the bound material.
    needed = set()
    pending = [graph.data[key] for key in ("surface", "displacement", "volume") if graph.data[key]]
    while pending:
        node = pending.pop()
        if node in needed:
            continue
        needed.add(node)
        pending.extend(c["source"] for c in graph.data["connections"] if c["target"] == node)
    graph.data["nodes"] = [node for node in graph.data["nodes"] if node["id"] in needed]
    graph.data["connections"] = [c for c in graph.data["connections"] if c["target"] in needed]
    for terminal in TERMINALS:
        if terminal not in ("surface", "displacement", "volume"):
            graph.data[terminal] = None
    for node in graph.data["nodes"]:
        if graph.catalog.definition(node["shader"])["type"] not in SHADER_TYPES and node["shader"] not in PROJECTION_CAMERAS:
            raise ValueError("This material depends on scene-reference nodes that cannot be embedded as a USD material.")
        attrs = graph.catalog.attributes(node["shader"])
        for name, value in node["values"].items():
            if attrs[name].get("filename") and isinstance(value, str) and value and "://" not in value:
                path = Path(os.path.expandvars(value)).expanduser()
                node["values"][name] = str(path if path.is_absolute() else Path(base_dir) / path)
    graph.validate(require_surface=True)
    return graph


def unique_path(stage, parent, name):
    candidate = parent.AppendChild(name)
    suffix = 2
    while stage.GetPrimAtPath(candidate):
        candidate = parent.AppendChild(f"{name}_{suffix}")
        suffix += 1
    return candidate


def prepare_material_binding(prim):
    if prim.IsA(UsdGeom.Subset):
        subset = UsdGeom.Subset(prim)
        subset.CreateFamilyNameAttr(UsdShade.Tokens.materialBind)
        mesh_api = UsdShade.MaterialBindingAPI.Apply(prim.GetParent())
        if mesh_api.GetMaterialBindSubsetsFamilyType() != UsdGeom.Tokens.partition:
            mesh_api.SetMaterialBindSubsetsFamilyType(UsdGeom.Tokens.nonOverlapping)
        valid, reason = UsdGeom.Subset.ValidateFamily(UsdGeom.Imageable(prim.GetParent()), UsdGeom.Tokens.face,
                                                    UsdShade.Tokens.materialBind)
        if not valid:
            raise ValueError("Cannot bind this GeomSubset: " + reason)


def bind_graph_material(stage, prim, data, base_dir):
    return bind_graph_materials(stage, [prim], data, base_dir)


def bind_graph_materials(stage, prims, data, base_dir):
    if not prims or any(not can_bind_material(prim) for prim in prims):
        raise ValueError("Select geometry, a transform/scope, or a face GeomSubset on a mesh to bind a material.")
    graph = material_graph(data, base_dir)
    source = create_stage(graph, None)
    for prim in prims:
        prepare_material_binding(prim)
    root_path = Sdf.Path("/ShaderGraphMaterials")
    root = stage.GetPrimAtPath(root_path)
    if root and (not root.IsA(UsdGeom.Scope) or root.IsInstance() or not root.IsActive()):
        root_path = unique_path(stage, Sdf.Path.absoluteRootPath, "ShaderGraphMaterials")
    if not stage.GetPrimAtPath(root_path):
        UsdGeom.Scope.Define(stage, root_path)
    # One assignment shares one snapshot across its selected prims. Later
    # assignments still create independent snapshots of the edited graph.
    material_path = unique_path(stage, root_path, graph.material_export_name())
    layer = stage.GetEditTarget().GetLayer()
    Sdf.CreatePrimInLayer(layer, root_path)
    if not Sdf.CopySpec(source.GetRootLayer(), source.GetDefaultPrim().GetPath(), layer, material_path):
        raise ValueError("Could not copy the shader graph into the USD stage.")
    material = UsdShade.Material(stage.GetPrimAtPath(material_path))
    material.GetPrim().SetCustomDataByKey("moonrayEditor:source", "shaderGraph")
    from .usd_material_sync import associate
    associate(stage, material, graph, base_dir)
    for prim in prims:
        api = UsdShade.MaterialBindingAPI.Apply(prim)
        if not api.Bind(material):
            raise ValueError("USD could not bind the material to " + str(prim.GetPath()))
        # A full-purpose binding would otherwise hide the new all-purpose one.
        full = api.GetDirectBindingRel(UsdShade.Tokens.full)
        if full and full.HasAuthoredTargets():
            if not api.Bind(material, materialPurpose=UsdShade.Tokens.full):
                raise ValueError("USD could not bind the full material to " + str(prim.GetPath()))
    # Resolve after every assignment so selected ancestors/descendants work in
    # any selection order. StageEdits rolls back the entire batch on failure.
    for prim in prims:
        resolved, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(UsdShade.Tokens.full)
        if not resolved or resolved.GetPath() != material_path:
            raise ValueError("An ancestor or collection binding takes precedence on " + str(prim.GetPath()) +
                             ". No assignments were applied; adjust that binding before assigning here.")
    paths = [str(prim.GetPath()) for prim in prims]
    return dict(path=paths[0], paths=paths, material=str(material_path), name=graph.data["name"])
