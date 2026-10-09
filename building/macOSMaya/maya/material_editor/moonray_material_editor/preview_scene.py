"""Copy the evaluated native preview scene to editable USD (renderer process)."""
import json
import os
from pathlib import Path
import tempfile

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdRender, UsdShade, UsdVol

from .usd_compat import set_display_name
from .model import GraphError, LIGHT_SAMPLING, LOBE_SAMPLES, MATRIX_TYPES, RAY_DEPTHS, SHADER_TYPES, VECTOR_TYPES, PROJECTION_CAMERAS
from .native import create_scene
from .usd_schemas import register_schemas
from .worker import usd_type, usd_value


LIGHT_TYPES = {"RectLight": "RectLight", "DiskLight": "DiskLight", "SphereLight": "SphereLight",
               "CylinderLight": "CylinderLight", "DistantLight": "DistantLight", "EnvLight": "DomeLight",
               "SpotLight": "DiskLight", "MeshLight": "MoonrayMeshLight"}


def native_data(kind, value):
    if kind.endswith("Vector") or kind == "SceneObjectIndexable":
        return [native_data(kind[:-6], item) for item in value.toList()]
    if kind in MATRIX_TYPES:
        return [[value[row * MATRIX_TYPES[kind] + column] for column in range(MATRIX_TYPES[kind])] for row in range(MATRIX_TYPES[kind])]
    if kind in VECTOR_TYPES:
        return [value[i] for i in range(VECTOR_TYPES[kind])]
    return value


def attribute(prim, name, metadata, value):
    if metadata.get("enum"):
        choices = metadata["enum"]
        label = next((key for key, number in choices.items() if number == value), None)
        if label is None:
            raise GraphError(f"Unsupported enum value for {name}: {value}")
        prim.CreateAttribute(name, Sdf.ValueTypeNames.Token).Set(label)
    else:
        prim.CreateAttribute(name, usd_type(metadata)).Set(usd_value(metadata, value))


def define_light(stage, path, kind, values, metadata, geometry=None):
    """Preserve native values, including USD's cylinder axis and spot half-angle."""
    if kind not in LIGHT_TYPES:
        raise GraphError(f"{kind} cannot currently be converted by hdMoonray's USD bridge. Use Export scene RDL for this scene.")
    prim = stage.DefinePrim(path, LIGHT_TYPES[kind])
    light = UsdLux.LightAPI.Apply(prim)
    light.CreateColorAttr(Gf.Vec3f(*values["color"]))
    light.CreateIntensityAttr(values["intensity"])
    light.CreateExposureAttr(values["exposure"])
    light.CreateNormalizeAttr(values.get("normalized", False))
    mapped = {"color", "intensity", "exposure", "normalized", "node_xform", "on"}
    fields = {"radius": "radius", "width": "width", "height": "height", "angular_extent": "angle"}
    if kind == "CylinderLight":
        fields["height"] = "length"
        # This hdMoonray version reads height, while USD names it length.
        attribute(prim, "moonray:height", metadata["height"], values["height"])
    if kind == "SpotLight":
        fields["lens_radius"] = "radius"
        shaping = UsdLux.ShapingAPI.Apply(prim)
        outer = values["outer_cone_angle"]
        shaping.CreateShapingConeAngleAttr(outer / 2)
        shaping.CreateShapingConeSoftnessAttr(1 - values["inner_cone_angle"] / outer if outer else 0.)
        prim.CreateAttribute("moonray:class", Sdf.ValueTypeNames.Token).Set("SpotLight")
        mapped.update(("outer_cone_angle", "inner_cone_angle"))
    for name, usd_name in fields.items():
        if name in values:
            prim.CreateAttribute("inputs:" + usd_name, Sdf.ValueTypeNames.Float, custom=False).Set(values[name])
            mapped.add(name)
    if values.get("texture"):
        if kind in {"MeshLight", "DistantLight"}:
            raise GraphError(kind + " does not evaluate the inherited texture input. Use a supported textured area light.")
        prim.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset, custom=False).Set(Sdf.AssetPath(values["texture"]))
    mapped.add("texture")
    if kind == "MeshLight":
        if not geometry:
            raise GraphError("MeshLight requires a connected mesh geometry.")
        prim.CreateRelationship("inputs:geometry", custom=False).SetTargets([geometry])
        UsdGeom.Imageable(stage.GetPrimAtPath(geometry)).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    matrix = Gf.Matrix4d(*[v for row in values["node_xform"] for v in row])
    if kind == "CylinderLight":
        # The renderer rotates USD cylinders -90 degrees about Z on import.
        matrix = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), 90)) * matrix
    UsdGeom.Xformable(prim).AddTransformOp().Set(matrix)
    if not values.get("on", True):
        UsdGeom.Imageable(prim).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
    for name, value in values.items():
        if name not in mapped and name in metadata and not metadata[name]["attrType"].startswith("SceneObject"):
            attribute(prim, "moonray:" + name, metadata[name], value)
    return prim


def save_preview_scene(graph, destination, base_dir, size=512):
    """Save the full current preview; keep generated assets beside the USD."""
    destination = Path(destination).expanduser().absolute()
    if destination.suffix.lower() not in (".usd", ".usda", ".usdc"):
        raise GraphError("Save the preview scene as .usd, .usda, or .usdc.")
    # Keep the conversion's intermediate RDL separate from user files. Baked
    # light textures use persistent, content-addressed files outside scratch.
    with tempfile.TemporaryDirectory(prefix=".preview-usd-", dir=destination.parent) as directory:
        temporary = Path(directory) / destination.name
        result = copy_preview_scene(graph, temporary, base_dir, size,
            asset_directory=destination.parent / (destination.stem + "_assets"))
        temporary.replace(destination)
    return dict(result, path=str(destination))


def copy_preview_scene(graph, destination, base_dir, size=512, *, asset_directory=None):
    """Resolve the current graph, then copy actual layer assignments and objects."""
    import scene_rdl2 as rdl
    register_schemas()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    graph = graph.clone()
    for node in graph.data["nodes"]:
        for name, meta in graph.catalog.attributes(node["shader"]).items():
            value = graph.value(node, name)
            if meta.get("filename") and value and "://" not in value:
                asset = Path(os.path.expandvars(value)).expanduser()
                node["values"][name] = str(asset if asset.is_absolute() else Path(base_dir) / asset)
    if graph.data["preview"]["vdb_file"]:
        asset = Path(os.path.expandvars(graph.data["preview"]["vdb_file"])).expanduser()
        graph.data["preview"]["vdb_file"] = str(asset if asset.is_absolute() else Path(base_dir) / asset)
    # Compositing/AOV terminals cannot be represented as a USD material scene.
    if graph.data["display"] or graph.data["aov"] or graph.data["preview"]["render_view"] != "beauty":
        raise GraphError("Choose the beauty preview and disconnect display/AOV outputs before copying a USD scene. Export scene RDL preserves those outputs.")
    source = destination.with_suffix(".rdla")
    create_scene(graph, source, asset_directory or destination.parent / "assets", size)
    context = rdl.SceneContext()
    context.setProxyModeEnabled(True)
    rdl.AsciiReader(context).fromFile(str(source))
    scene = context.getSceneVariables()
    layer = scene.get("layer")
    if any(layer.get(name).toList() and any(layer.get(name).toList())
           for name in ("lightfiltersets", "shadowsets", "shadowreceiversets")):
        raise GraphError("This preview uses renderer sets that cannot yet be copied to USD. Export scene RDL preserves them.")
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/PreviewScene")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    set_display_name(root.GetPrim(), graph.data["name"] + " preview")
    stage.GetRootLayer().customLayerData = {"lunatic:previewGraph": json.dumps(graph.data)}
    paths, shaders, materials = {}, {}, {}

    def path(obj):
        if obj.getName() not in paths:
            paths[obj.getName()] = Sdf.Path("/PreviewScene" + obj.getName())
        return paths[obj.getName()]

    def values(obj):
        return {name: native_data(kind, obj.get(name)) for name, kind in obj.getAttributeNamesAndTypes().items()}

    def shader(obj):
        if obj.getName() in shaders:
            return shaders[obj.getName()]
        kind = obj.getSceneClass().getName()
        if kind not in graph.catalog.shaders or (graph.catalog.definition(kind)["type"] not in SHADER_TYPES and kind not in PROJECTION_CAMERAS):
            raise GraphError("Cannot copy shader reference " + kind + " to USD.")
        result = UsdShade.Shader.Define(stage, path(obj))
        shaders[obj.getName()] = result
        result.CreateIdAttr(kind)
        category = graph.catalog.category(kind)
        result.CreateOutput("out", Sdf.ValueTypeNames.Token if category in {"Volume", "Camera"} else
                            Sdf.ValueTypeNames.Color4f if category == "Material" else Sdf.ValueTypeNames.Color3f)
        for name, meta in graph.catalog.attributes(kind).items():
            value = obj.get(name)
            if meta["attrType"].startswith("SceneObject"):
                if not value:
                    continue
                if meta["attrType"] != "SceneObject*":
                    if value.toList():
                        raise GraphError("Cannot copy shader object list: " + kind + "." + name)
                    continue
                result.CreateInput(name, usd_type(meta)).ConnectToSource(shader(value).GetOutput("out"))
            else:
                data = native_data(meta["attrType"], value)
                binding = None
                if obj.getAttributeAt(obj.getAttributeNamesAndIndices()[name]).isBindable():
                    binding = obj.getBinding(name)
                if data == meta.get("default") and not binding:
                    continue
                if meta.get("enum"):
                    token = next((key for key, number in meta["enum"].items() if number == data), None)
                    inp = result.CreateInput(name, Sdf.ValueTypeNames.Token)
                    inp.Set(token)
                else:
                    inp = result.CreateInput(name, usd_type(meta))
                    inp.Set(usd_value(meta, data))
                if binding:
                    inp.ConnectToSource(shader(binding).GetOutput("out"))
        return result

    def geometry(obj):
        if stage.GetPrimAtPath(path(obj)):
            return stage.GetPrimAtPath(path(obj))
        kind, data = obj.getSceneClass().getName(), values(obj)
        if kind == "RdlMeshGeometry":
            mesh = UsdGeom.Mesh.Define(stage, path(obj))
            mesh.CreatePointsAttr(data["vertex_list_0"])
            mesh.CreateFaceVertexCountsAttr(data["face_vertex_count"])
            mesh.CreateFaceVertexIndicesAttr(data["vertices_by_index"])
            mesh.CreateOrientationAttr("leftHanded" if data["orientation"] else "rightHanded")
            mesh.CreateSubdivisionSchemeAttr(("bilinear" if data["subd_scheme"] == 0 else "catmullClark") if data["is_subd"] else "none")
            mesh.CreateInterpolateBoundaryAttr(("none", "edgeOnly", "edgeAndCorner")[data["subd_boundary"]])
            mesh.CreateFaceVaryingLinearInterpolationAttr(("none", "cornersOnly", "cornersPlus1", "cornersPlus2", "boundaries", "all")[data["subd_fvar_linear"]])
            if data["subd_crease_indices"]:
                mesh.CreateCreaseIndicesAttr(data["subd_crease_indices"])
                mesh.CreateCreaseLengthsAttr([2] * (len(data["subd_crease_indices"]) // 2))
                mesh.CreateCreaseSharpnessesAttr(data["subd_crease_sharpnesses"])
            if data["subd_corner_indices"]:
                mesh.CreateCornerIndicesAttr(data["subd_corner_indices"])
                mesh.CreateCornerSharpnessesAttr(data["subd_corner_sharpnesses"])
            if data["normal_list"]:
                mesh.CreateNormalsAttr(data["normal_list"])
                mesh.SetNormalsInterpolation("faceVarying" if len(data["normal_list"]) == len(data["vertices_by_index"]) else "vertex")
            prim = mesh.GetPrim()
            if data["uv_list"]:
                interpolation = "faceVarying" if len(data["uv_list"]) == len(data["vertices_by_index"]) else "vertex"
                UsdGeom.PrimvarsAPI(prim).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, interpolation).Set(data["uv_list"])
            extras = ("mesh_resolution", "adaptive_error", "smooth_normal")
        elif kind == "RdlCurveGeometry":
            curves = UsdGeom.BasisCurves.Define(stage, path(obj))
            curves.CreatePointsAttr(data["vertex_list_0"])
            curves.CreateCurveVertexCountsAttr(data["curves_vertex_count"])
            curves.CreateTypeAttr("linear" if data["curve_type"] == 0 else "cubic")
            curves.CreateBasisAttr("bezier" if data["curve_type"] == 1 else "bspline")
            curves.CreateWidthsAttr([v * 2 for v in data["radius_list"]])
            curves.SetWidthsInterpolation("vertex")
            prim = curves.GetPrim()
            if data["uv_list"]:
                UsdGeom.PrimvarsAPI(prim).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "uniform").Set(data["uv_list"])
            extras = ("curves_subtype", "tessellation_rate")
        elif kind == "VdbGeometry":
            volume = UsdVol.Volume.Define(stage, path(obj))
            for name in ("density", "emission", "velocity"):
                grid = data[name + "_grid"]
                if grid:
                    field = UsdVol.OpenVDBAsset.Define(stage, path(obj).AppendChild(name))
                    field.CreateFilePathAttr(Sdf.AssetPath(data["model"]))
                    field.CreateFieldNameAttr(grid)
                    volume.CreateFieldRelationship(name, field.GetPath())
            prim = volume.GetPrim()
            extras = ("velocity_scale", "velocity_sample_rate", "emission_sample_rate")
        else:
            raise GraphError("Cannot copy preview geometry " + kind + ". Use Export scene RDL.")
        UsdGeom.Xformable(prim).AddTransformOp().Set(Gf.Matrix4d(*[v for row in data["node_xform"] for v in row]))
        metadata = graph.catalog.attributes(kind)
        extras += tuple(n for n in data if n.startswith("visible_") or n in {"ray_epsilon", "shadow_ray_epsilon", "side_type", "reverse_normals"})
        for name in extras:
            attribute(prim, "primvars:moonray:" + name, metadata[name], data[name])
            prim.GetAttribute("primvars:moonray:" + name).SetMetadata("interpolation", "constant")
        return prim

    geometries = layer.get("geometries").toList()
    lights, links = {}, {}
    for index, obj in enumerate(geometries):
        if layer.get("parts").toList()[index]:
            raise GraphError("Preview part assignments cannot yet be copied to USD.")
        prim = geometry(obj)
        terminals = [layer.get(n).toList()[index] for n in ("surface_shaders", "displacements", "volume_shaders")]
        key = tuple(o.getName() if o else None for o in terminals)
        if key not in materials:
            mat = UsdShade.Material.Define(stage, "/PreviewScene/Materials/Material" + str(len(materials)))
            materials[key] = mat
            for name, source_obj in zip(("surface", "displacement", "volume"), terminals):
                if source_obj:
                    mat.CreateOutput("moonray:" + name, Sdf.ValueTypeNames.Token).ConnectToSource(shader(source_obj).GetOutput("out"))
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(materials[key])
        active_names = {"/Graph/" + graph.material_export_name() + "/" + graph.export_names()[graph.data[t]]
                        for t in ("surface", "displacement", "volume") if graph.data[t]}
        if any(name in active_names for name in key if name):
            from .usd_material_sync import associate
            associate(stage, materials[key], graph, base_dir, "/PreviewScene/Graph/" + graph.material_export_name() + "/")
        lightset = layer.get("lightsets").toList()[index]
        if lightset:
            for source_light in lightset.get("lights").toList():
                lights[source_light.getName()] = source_light
                links.setdefault(source_light.getName(), []).append(prim.GetPath())
    for name, obj in lights.items():
        data, kind = values(obj), obj.getSceneClass().getName()
        if data.get("light_filters"):
            raise GraphError("Light filters cannot yet be copied through this USD scene exporter. Use Export scene RDL.")
        if kind == "MeshLight" and data.get("map_shader"):
            raise GraphError("MeshLight map_shader is not supported by the current USD renderer bridge.")
        geom = geometry(data["geometry"]).GetPath() if kind == "MeshLight" and data["geometry"] else None
        prim = define_light(stage, path(obj), kind, data, graph.catalog.attributes(kind), geom)
        collection = UsdLux.LightAPI(prim).GetLightLinkCollectionAPI()
        collection.CreateIncludeRootAttr(False)
        collection.CreateIncludesRel().SetTargets(links[name])
    camera = scene.get("camera")
    data, kind = values(camera), camera.getSceneClass().getName()
    if kind not in {"PerspectiveCamera", "OrthographicCamera"}:
        raise GraphError("Cannot copy camera " + kind + " to USD.")
    render_camera_path = path(camera)
    if stage.GetPrimAtPath(render_camera_path):
        from .usd_materials import unique_path
        render_camera_path = unique_path(stage, render_camera_path.GetParentPath(), render_camera_path.name + "_RenderCamera")
    cam = UsdGeom.Camera.Define(stage, render_camera_path)
    from .usd_preview_camera import associate as associate_camera, write_camera
    write_camera(cam, kind, data, graph.catalog)
    cam.SetResetXformStack(True)
    associate_camera(cam, graph, base_dir)
    settings = UsdRender.Settings.Define(stage, "/PreviewScene/RenderSettings")
    settings.CreateCameraRel().SetTargets([cam.GetPath()])
    settings.CreateResolutionAttr(Gf.Vec2i(size, size))
    stage.SetMetadata("renderSettingsPrimPath", str(settings.GetPath()))
    from .usd_render_config import SCENE_PREFIX
    preview = graph.data["preview"]
    render_values = {SCENE_PREFIX + key: preview[key] for key in (*RAY_DEPTHS, *LOBE_SAMPLES, *LIGHT_SAMPLING, "shadow_terminator_fix")}
    render_values.update({SCENE_PREFIX + "pixel_samples": preview["samples"],
                          SCENE_PREFIX + "enable_displacement": preview["displacement_enabled"], "enableMotionBlur": False})
    stage.GetRootLayer().customLayerData = {**stage.GetRootLayer().customLayerData,
        "lunatic:previewCamera": str(cam.GetPath()), "lunatic:previewSettings": json.dumps(render_values)}
    if not stage.Export(str(destination)):
        raise GraphError("Could not write the preview USD scene.")
    return dict(path=str(destination), camera=str(cam.GetPath()), size=size)
