"""Inspect materials and copy prepared conversions to the selected destination."""
import hashlib
import json
import os
from pathlib import Path
import re

from pxr import Sdf, Usd, UsdGeom, UsdShade

from .material_conversion import DESTINATIONS, mdl_literal, recipe, recommendation, split_arguments
from .model import Graph
from .usd_composition import new_sublayer
from .usd_editing import encode_value
from .usd_materials import material_catalog, unique_path
from .usd_project import anchor_asset
from .usd_shader_graph import resolve_source
from .worker import create_stage


def asset_value(attr, time):
    value = attr.Get(time)
    if isinstance(value, Sdf.AssetPath):
        if value.resolvedPath:
            return value.resolvedPath
        stack = attr.GetPropertyStack(time)
        return anchor_asset(stack[0].layer, value.path) if stack else value.path
    return encode_value(value)


def preset_values(shader, family, *, metadata=None):
    """Read simple named literal defaults from a local MDL wrapper, never execute it."""
    attr = shader.GetPrim().GetAttribute("info:mdl:sourceAsset")
    path = asset_value(attr, Usd.TimeCode.Default()) if attr else ""
    result, notes = {}, []
    if not path or not Path(path).is_file():
        return result, ["MDL module defaults are unavailable through its resolved file path."]
    file = Path(path)
    if file.stat().st_size > 2_000_000:
        return result, ["MDL module is too large for literal-preset inspection."]
    text = file.read_text(errors="replace")
    # Preserve quoted filenames when removing comments.
    text = re.sub(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
                  lambda m: m[0] if m[0].startswith('"') else " ", text, flags=re.S)
    match = re.search(r"export\s+material\s+" + re.escape(family) +
                      r"\s*\(\s*\*\s*\)\s*=\s*([\w:]+)\s*\((.*)\)\s*;\s*$", text, re.S)
    if not match:
        return result, ["MDL module is not a supported literal preset; its defaults/procedurals were not evaluated."]
    if metadata is not None:
        metadata["mdl_base"] = match[1]
    for arg in split_arguments(match[2]):
        pair = re.match(r"^(\w+)\s*:\s*(.*)$", arg, re.S)
        if not pair:
            continue
        name, expression = pair.groups()
        try:
            result[name] = mdl_literal(expression.strip())
        except ValueError:
            texture = re.fullmatch(r'texture_2d\(\s*("(?:\\.|[^"\\])*")?(?:\s*,\s*[\w:]+)?\s*\)', expression.strip())
            if texture:
                value = json.loads(texture[1]) if texture[1] else ""
                result[name] = str((file.parent / value).resolve()) if value else ""
            else:
                notes.append("Preset expression not translated: " + name)
    return result, notes


def source_record(material, frame):
    path = str(material.GetPath())
    source = dict(path=path, name=material.GetPrim().GetName(), family="No surface",
                  values={}, connections=[], notes=[], shaders=[], mdl=False,
                  meters_per_unit=UsdGeom.GetStageMetersPerUnit(material.GetPrim().GetStage()))
    for context in ("moonray:surface", "mdl:surface", "surface"):
        output = material.GetOutput(context)
        if not output or not output.GetAttr().GetConnections():
            continue
        try:
            attr = resolve_source(output.GetAttr())
            shader = UsdShade.Shader(attr.GetPrim()) if attr else None
            if not shader:
                raise ValueError("Surface output does not resolve to a Shader.")
            source["shader"] = str(shader.GetPath())
            family = shader.GetIdAttr().Get() or shader.GetPrim().GetAttribute("info:mdl:sourceAsset:subIdentifier").Get() or "Unknown MDL"
            source["family"] = str(family)
            source["mdl"] = context == "mdl:surface"
            source["native"] = context == "moonray:surface" or (
                family in material_catalog().shaders and family != "UsdPreviewSurface"
                and material_catalog().category(family) == "Material")
            if family == "UsdPreviewSurface":
                defaults = material_catalog().attributes("UsdPreviewSurface")
                source["values"] = {name: defaults[name]["default"] for name in (
                    "diffuseColor", "roughness", "metallic", "ior", "opacity",
                    "emissiveColor", "clearcoat", "clearcoatRoughness") if name in defaults}
            if source["mdl"]:
                source["values"], source["notes"] = preset_values(shader, str(family), metadata=source)
            time = Usd.TimeCode(frame)
            for port in shader.GetInputs():
                attr = port.GetAttr()
                name = str(port.GetBaseName())
                if attr.GetConnections():
                    source["connections"].append(name)
                    source["values"].pop(name, None)
                elif attr.HasAuthoredValueOpinion():
                    value = asset_value(attr, time)
                    if value is not None:
                        source["values"][name] = value
                if attr.ValueMightBeTimeVarying():
                    source["animated"] = True
            source["extra_outputs"] = any(
                o.GetBaseName() in ("volume", "displacement") and o.GetAttr().GetConnections()
                and not source["mdl"] for o in material.GetOutputs())
        except (ValueError, RuntimeError, OSError) as exc:
            source["notes"].append(str(exc))
        break
    return source


def scene_material_records(stage):
    """Material/shader names and full paths, including externally located networks."""
    result = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Material):
            continue
        material = UsdShade.Material(prim)
        shaders = {str(p.GetPath()): p for p in Usd.PrimRange(prim) if p.IsA(UsdShade.Shader)}
        pending = [o.GetAttr() for o in material.GetOutputs()]
        visited = set()
        while pending:
            attr = pending.pop()
            key = str(attr.GetPath())
            if key in visited:
                continue
            visited.add(key)
            for connection in attr.GetConnections():
                upstream = stage.GetAttributeAtPath(connection)
                if not upstream:
                    continue
                pending.append(upstream)
                parent = upstream.GetPrim()
                if parent.IsA(UsdShade.Shader) and str(parent.GetPath()) not in shaders:
                    shaders[str(parent.GetPath())] = parent
                if parent.IsA(UsdShade.Shader):
                    pending.extend(i.GetAttr() for i in UsdShade.Shader(parent).GetInputs())
        rows = []
        for path, shader in sorted(shaders.items()):
            family = shader.GetAttribute("info:id").Get() or shader.GetAttribute("info:mdl:sourceAsset:subIdentifier").Get() or "Unknown"
            rows.append(dict(path=path, name=shader.GetName(), family=str(family),
                             supported=family in material_catalog().shaders))
        native = material.GetOutput("moonray:surface")
        mdl = material.GetOutput("mdl:surface")
        result.append(dict(path=str(prim.GetPath()), name=prim.GetName(), shaders=rows,
                           conversion=bool(mdl and mdl.GetAttr().GetConnections() and not
                                           (native and native.GetAttr().GetConnections()))))
    if result:
        from .usd_material_bindings import bound_material_paths
        bound = bound_material_paths(stage)
        for row in result:
            row["has_bound_prims"] = row["path"] in bound
    return sorted(result, key=lambda r: (r["name"].casefold(), r["path"]))


def inventory(stage, frame=0):
    rows = {str(p.GetPath()): source_record(UsdShade.Material(p), frame)
            for p in stage.Traverse() if p.IsA(UsdShade.Material)}
    bindings = []
    for prim in stage.Traverse():
        for rel in prim.GetRelationships():
            if not (rel.GetName() == "material:binding" or rel.GetName().startswith("material:binding:")):
                continue
            targets = rel.GetTargets()
            if not targets:
                continue
            # The material is the final target of direct and collection bindings.
            path = str(targets[-1])
            if path not in rows:
                rows[path] = dict(path=path, name=targets[-1].name, family="Missing material",
                                  missing=True, values={}, connections=[], notes=[])
            bindings.append(dict(path=str(rel.GetPath()), targets=list(map(str, targets)),
                                 strength=str(UsdShade.MaterialBindingAPI.GetMaterialBindingStrength(rel))))
    for path, row in rows.items():
        row["bindings"] = sum(b["targets"][-1] == path for b in bindings)
        row["recommended"], row["reason"] = recommendation(row)
    records = sorted(rows.values(), key=lambda r: (r["name"].casefold(), r["path"]))
    fingerprint = hashlib.sha256(json.dumps(dict(rows=records, bindings=bindings, frame=frame), sort_keys=True).encode()).hexdigest()
    return dict(rows=records, bindings=bindings, fingerprint=fingerprint, frame=frame,
                edit_target=stage.GetEditTarget().GetLayer().identifier,
                destinations=[name for name in DESTINATIONS if name in material_catalog().shaders])


def resolved_bindings(stage):
    result = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Gprim) and not prim.IsA(UsdGeom.Subset):
            continue
        for purpose in (UsdShade.Tokens.allPurpose, UsdShade.Tokens.full, UsdShade.Tokens.preview):
            material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(purpose)
            result[str(prim.GetPath()), str(purpose)] = str(material.GetPath()) if material else None
    return result


def write_binding_overrides(stage, bindings, mapping):
    count = 0
    for binding in bindings:
        source = binding["targets"][-1]
        if source not in mapping:
            continue
        path = Sdf.Path(binding["path"])
        prim = stage.OverridePrim(path.GetPrimPath())
        UsdShade.MaterialBindingAPI.Apply(prim)
        relationship = prim.CreateRelationship(path.name, custom=False)
        if not relationship.SetTargets([*binding["targets"][:-1], mapping[source]]):
            raise ValueError("Could not author the material binding at " + str(path))
        UsdShade.MaterialBindingAPI.SetMaterialBindingStrength(relationship, binding["strength"])
        count += 1
    return count


def check_converted_bindings(stage, before, mapping):
    after = resolved_bindings(stage)
    for key, source in before.items():
        if after.get(key) != mapping.get(source, source):
            raise ValueError("A stronger binding prevents this conversion at " + key[0] +
                             ". No conversion was applied; choose a stronger edit target.")


def convert_materials(editing, data):
    stage = editing.stage
    storage = data.get("storage", "target")
    if storage not in ("target", "memory", "disk"):
        raise ValueError("Choose the current edit target or a new conversion layer.")
    target = stage.GetEditTarget()
    destination_layer = target.GetLayer()
    root = stage.GetSessionLayer()
    if storage == "target":
        if data.get("target") and data["target"] != destination_layer.identifier:
            raise ValueError("The edit target changed. Refresh the conversion table before applying.")
        if destination_layer not in stage.GetLayerStack() or not destination_layer.permissionToEdit:
            raise ValueError("Choose an editable, unmuted layer in the local layer stack as the edit target.")
    elif not root.permissionToEdit or stage.IsLayerMuted(root.identifier):
        raise ValueError("The session layer does not permit adding a contributing conversion sublayer.")
    report = inventory(stage, data["frame"])
    if report["fingerprint"] != data.get("fingerprint"):
        raise ValueError("Materials or bindings changed. Refresh the conversion table before applying.")
    rows = {r["path"]: r for r in report["rows"]}
    choices = data.get("choices", [])
    if not choices or len({c["source"] for c in choices}) != len(choices):
        raise ValueError("Select one or more distinct source materials.")
    scope = unique_path(stage, Sdf.Path.absoluteRootPath, "MoonrayLooks")
    prepared = Sdf.Layer.CreateAnonymous("MoonrayLooks.usda")
    output = Usd.Stage.Open(prepared)
    UsdGeom.Scope.Define(output, scope)
    mapping = {}
    for choice in choices:
        source = rows.get(choice["source"])
        if not source:
            raise ValueError("Source material is no longer in the table.")
        plan = recipe(material_catalog(), source, choice["destination"])
        if not plan["graph"]:
            raise ValueError("This source requires a manual rebuild: " + source["path"])
        if plan["status"] == "Approximation" and choice.get("accept_approximation") is not True:
            raise ValueError("Review the approximate mapping before selecting " + source["name"])
        graph = Graph(material_catalog(), plan["graph"])
        material_stage = create_stage(graph, None)
        name = source["name"] + "_MOONRAY"
        destination = scope.AppendChild(name)
        if output.GetPrimAtPath(destination):
            # Keep identical leaf names from different source namespaces intact.
            parent_name = Sdf.Path(source["path"]).GetParentPath().name or "Materials"
            parent = unique_path(output, scope, parent_name)
            UsdGeom.Scope.Define(output, parent)
            destination = parent.AppendChild(name)
        if not Sdf.CopySpec(material_stage.GetRootLayer(), material_stage.GetDefaultPrim().GetPath(), prepared, destination):
            raise ValueError("Could not create the converted material.")
        material = UsdShade.Material.Get(output, destination)
        original = UsdShade.Material.Get(stage, source["path"])
        # Keep original renderer terminals by forwarding to their existing graphs.
        for terminal in original.GetOutputs():
            if terminal.GetFullName().startswith("outputs:moonray:"):
                continue
            attr = material.CreateOutput(terminal.GetFullName().removeprefix("outputs:"), terminal.GetTypeName()).GetAttr()
            attr.SetConnections(terminal.GetAttr().GetConnections())
            value = terminal.GetAttr().Get()
            if value is not None:
                attr.Set(value)
        material.GetPrim().SetCustomDataByKey("lunatic:conversion", dict(
            source=source["path"], family=source["family"], destination=choice["destination"],
            version=1, status=plan["status"], frame=data["frame"], report=json.dumps({k: v for k, v in plan.items() if k != "graph"})))
        mapping[source["path"]] = str(destination)
    binding_count = write_binding_overrides(output, report["bindings"], mapping)
    before = resolved_bindings(stage)
    if storage == "target":
        spec_path = target.MapToSpecPath(scope)
        if spec_path.isEmpty or destination_layer.GetPrimAtPath(spec_path):
            raise ValueError("The edit target cannot receive the new material scope. Choose another target.")
        def copy_to_target():
            # The material subtree is new. Copy it once; merge bindings through
            # USD authoring so existing prim definitions, APIs and properties stay.
            Sdf.CreatePrimInLayer(destination_layer, spec_path.GetParentPath())
            if not Sdf.CopySpec(prepared, scope, destination_layer, spec_path):
                raise ValueError("Could not copy the converted materials to the edit target.")
            write_binding_overrides(stage, report["bindings"], mapping)
            check_converted_bindings(stage, before, mapping)
        editing.change("Convert materials to MoonRay", copy_to_target, layer=destination_layer)
        return dict(identifier=destination_layer.identifier, materials=mapping, bindings=binding_count, created_layer=False)

    child, created_file = new_sublayer(data)
    try:
        child.TransferContent(prepared)
        if created_file and not child.Export(str(created_file)):
            raise ValueError("Could not save the converted material layer.")
        link = child.identifier
        if created_file and root.realPath and Path(root.realPath).is_file():
            link = Path(os.path.relpath(created_file, Path(root.realPath).parent)).as_posix()
        # Hold the complete conversion layer across undo; only its parent link is
        # changed in this transaction, so one Undo restores all original bindings.
        editing.track_layer(child)
        def attach():
            root.subLayerPaths.insert(0, link)
            check_converted_bindings(stage, before, mapping)
        editing.change("Convert materials to MoonRay", attach, layer=root)
    except Exception:
        editing.saved.pop(child, None)
        editing.initial.pop(child, None)
        if created_file:
            created_file.unlink(missing_ok=True)
        raise
    editing.set_edit_target(child.identifier)
    return dict(identifier=child.identifier, materials=mapping, bindings=binding_count, created_layer=True)
