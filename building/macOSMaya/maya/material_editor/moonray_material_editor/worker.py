"""Run under MoonRay's Python 3.10 environment; never imports Qt."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
import subprocess
import sys

from .usd_compat import set_display_name
from .model import Catalog, Graph, GraphError, GLITTER_SPACE_FIELDS, LIGHT_SAMPLING, LOBE_SAMPLES, MATRIX_TYPES, RAY_DEPTHS, SHADER_TYPES, VECTOR_TYPES, PROJECTION_CAMERAS
from .render_progress import DEFAULT_PROGRESS_STEP, PROGRESS_STEPS, RenderProgress
from .render_region import merge_region, native_region, preserve_display_region, validate_region


def usd_type(attr):
    from pxr import Sdf
    names = {"Bool": "Bool", "Int": "Int", "Long": "Int64", "Float": "Float", "Double": "Double",
             "String": "String", "Rgb": "Color3f", "Rgba": "Color4f", "Vec2f": "Float2",
             "Vec3f": "Float3", "Vec4f": "Float4", "Vec2d": "Double2", "Vec3d": "Double3", "Vec4d": "Double4"}
    names.update({"Mat3f": "Matrix3d", "Mat3d": "Matrix3d", "Mat4f": "Matrix4d", "Mat4d": "Matrix4d"})
    t = attr["attrType"]
    if t == "SceneObject*":
        if attr.get("interface") == "Camera":
            return Sdf.ValueTypeNames.Token
        if attr.get("interface") == "Volume":
            return Sdf.ValueTypeNames.Token
        if attr.get("interface") in {"NormalMap", "Displacement"}:
            return Sdf.ValueTypeNames.Float3
        if attr.get("interface") in {"Material", "DwaBaseLayerable", "DwaBaseHairLayerable"}:
            return Sdf.ValueTypeNames.Color4f
        return Sdf.ValueTypeNames.Color3f
    array = t.endswith("Vector")
    if array:
        t = t[:-6]
    return getattr(Sdf.ValueTypeNames, names[t] + ("Array" if array else ""))


def usd_value(attr, value):
    from pxr import Gf
    t = attr["attrType"]
    if t.endswith("Vector"):
        return [usd_value({"attrType": t[:-6]}, v) for v in value]
    if t in VECTOR_TYPES:
        kind = "Vec3f" if t == "Rgb" else "Vec4f" if t == "Rgba" else t
        return getattr(Gf, kind)(*value)
    if t in MATRIX_TYPES:
        return getattr(Gf, "Matrix" + str(MATRIX_TYPES[t]) + "d")(*[component for row in value for component in row])
    return value


def create_preview_sphere(stage, material, settings):
    """Closed sphere with welded poles/seam and separate face-varying UVs."""
    from pxr import Gf, Sdf, UsdGeom, UsdShade
    sphere = UsdGeom.Mesh.Define(stage, "/Preview/Sphere")
    rows, cols = 24, 48
    points = [Gf.Vec3f(0, 1, 0)]
    for y in range(1, rows):
        theta = math.pi * y / rows
        for x in range(cols):
            phi = 2 * math.pi * x / cols
            points.append(Gf.Vec3f(math.sin(theta) * math.cos(phi), math.cos(theta), math.sin(theta) * math.sin(phi)))
    south = len(points)
    points.append(Gf.Vec3f(0, -1, 0))
    indices, counts, st = [], [], []

    def ring(y, x):
        return 1 + (y - 1) * cols + x % cols

    def face(vertices, uv):
        counts.append(len(vertices))
        indices.extend(vertices)
        st.extend(Gf.Vec2f(*v) for v in uv)

    for x in range(cols):
        u0, u1 = x / cols, (x + 1) / cols
        face([0, ring(1, x + 1), ring(1, x)],
             [((u0 + u1) / 2, 1), (u1, 1 - 1 / rows), (u0, 1 - 1 / rows)])
        for y in range(1, rows - 1):
            v0, v1 = 1 - y / rows, 1 - (y + 1) / rows
            face([ring(y, x), ring(y, x + 1), ring(y + 1, x + 1), ring(y + 1, x)],
                 [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
        face([ring(rows - 1, x), ring(rows - 1, x + 1), south],
             [(u0, 1 / rows), (u1, 1 / rows), ((u0 + u1) / 2, 0)])
    sphere.CreatePointsAttr(points)
    sphere.CreateFaceVertexCountsAttr(counts)
    sphere.CreateFaceVertexIndicesAttr(indices)
    sphere.CreateSubdivisionSchemeAttr(settings["subdivision_scheme"])
    if settings["subdivision_scheme"] == "none":
        sphere.CreateNormalsAttr(points)
        sphere.SetNormalsInterpolation("vertex")
    primvars = UsdGeom.PrimvarsAPI(sphere)
    primvars.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "faceVarying").Set(st)
    primvars.CreatePrimvar("moonray:mesh_resolution", Sdf.ValueTypeNames.Float, "constant").Set(float(settings["mesh_resolution"]))
    primvars.CreatePrimvar("moonray:adaptive_error", Sdf.ValueTypeNames.Float, "constant").Set(settings["adaptive_error"])
    UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
    return sphere


def create_stage(graph, path, preview=False):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade
    from .shader_adapters import native_graph
    graph.validate(require_surface=True)
    from .tx_cache import prepared_graph
    graph = prepared_graph(graph)
    graph = native_graph(graph)
    for node in graph.data["nodes"]:
        if graph.catalog.definition(node["shader"])["type"] not in SHADER_TYPES and node["shader"] not in PROJECTION_CAMERAS:
            raise GraphError("This graph contains scene, lighting, or image-processing nodes. Use Export scene RDL to preserve the complete graph.")
    stage = Usd.Stage.CreateInMemory() if path is None else Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    # Preview uses an internal path so names such as Preview cannot collide with
    # its geometry/camera. Standalone exports use the user's material identifier.
    material_path = "/Material" if preview else "/" + graph.material_export_name()
    material = UsdShade.Material.Define(stage, material_path)
    set_display_name(material.GetPrim(), graph.data["name"])
    stage.SetDefaultPrim(material.GetPrim())
    names = graph.export_names()
    shaders = {}
    for node in graph.data["nodes"]:
        shader = UsdShade.Shader.Define(stage, material_path + "/" + names[node["id"]])
        shader.CreateIdAttr(node["shader"])
        set_display_name(shader.GetPrim(), node["label"])
        shader.GetPrim().SetCustomDataByKey("editor:nodeId", node["id"])
        if node.get("usd_camera"):
            shader.GetPrim().SetCustomDataByKey("editor:usdCamera", node["usd_camera"])
        if node.get("usd_transform"):
            shader.GetPrim().SetCustomDataByKey("editor:usdTransform", node["usd_transform"])
        shader.GetPrim().SetCustomDataByKey("editor:position", Gf.Vec2d(*node["position"]))
        attrs = graph.catalog.attributes(node["shader"])
        for name, value in node["values"].items():
            attr = attrs[name]
            if name in GLITTER_SPACE_FIELDS and attr.get("enum"):
                # hdMoonray expects the enum label for glitter space in USD.
                choice = next(key for key, number in attr["enum"].items() if number == value)
                shader.CreateInput(name, Sdf.ValueTypeNames.Token).Set(choice)
            else:
                shader.CreateInput(name, usd_type(attr)).Set(usd_value(attr, value))
        category = graph.catalog.category(node["shader"])
        is_material = category == "Material"
        output_type = Sdf.ValueTypeNames.Token if category in {"Volume", "Camera"} else Sdf.ValueTypeNames.Color4f if is_material else (
            Sdf.ValueTypeNames.Float3 if graph.catalog.category(node["shader"]) in {"NormalMap", "Displacement"} else Sdf.ValueTypeNames.Color3f)
        shader.CreateOutput("surface" if is_material else "out", output_type)
        shaders[node["id"]] = shader
        if node["shader"] == "UsdUVTexture":
            for channel in ("rgb", "r", "g", "b", "a"):
                shader.CreateOutput(channel, Sdf.ValueTypeNames.Color3f if channel == "rgb" else Sdf.ValueTypeNames.Float)
    adapters = {}
    def source_output(node_id, channel):
        node = graph.node(node_id)
        shader = shaders[node_id]
        if node["shader"] == "UsdUVTexture":
            return shader.GetOutput("rgb" if channel == "out" else channel)
        if channel == "out":
            return shader.GetOutput("surface" if graph.catalog.category(node["shader"]) == "Material" else "out")
        if (node_id, channel) not in adapters:
            adapter_path = material_path + "/editor_channel_" + str(len(adapters))
            while stage.GetPrimAtPath(adapter_path):
                adapter_path += "_"
            adapter = UsdShade.Shader.Define(stage, adapter_path)
            if channel == "a":
                adapter.CreateIdAttr("ImageMap")
                attrs = graph.catalog.attributes(node["shader"])
                for name, v in node["values"].items():
                    adapter.CreateInput(name, usd_type(attrs[name])).Set(usd_value(attrs[name], v))
                adapter.CreateInput("alpha_only", Sdf.ValueTypeNames.Bool).Set(True)
                # Replicate this image node's incoming coordinate/control connections.
                for c in graph.data["connections"]:
                    if c["target"] == node_id:
                        adapter.CreateInput(c["input"], usd_type(attrs[c["input"]])).ConnectToSource(source_output(c["source"], c.get("output", "out")))
            else:
                adapter.CreateIdAttr("RgbToFloatMap")
                adapter.CreateInput("input", Sdf.ValueTypeNames.Color3f).ConnectToSource(shader.GetOutput("out"))
                adapter.CreateInput("mode", Sdf.ValueTypeNames.Int).Set({"r": 0, "g": 1, "b": 2}[channel])
            adapters[node_id, channel] = adapter.CreateOutput("out", Sdf.ValueTypeNames.Color3f)
            adapter.GetPrim().SetCustomDataByKey("editor:channelSource", node_id)
            adapter.GetPrim().SetCustomDataByKey("editor:channel", channel)
        return adapters[node_id, channel]
    for c in graph.data["connections"]:
        dst = graph.node(c["target"])
        attr = graph.catalog.attributes(dst["shader"])[c["input"]]
        if attr["attrType"] in {"SceneObjectVector", "SceneObjectIndexable"}:
            raise GraphError("Object-array connections require Export scene RDL")
        shaders[c["target"]].CreateInput(c["input"], usd_type(attr)).ConnectToSource(source_output(c["source"], c.get("output", "out")))
    # Match hdMoonray's native material terminal convention.
    if graph.data["surface"]:
        material.CreateOutput("moonray:surface", Sdf.ValueTypeNames.Color4f).ConnectToSource(shaders[graph.data["surface"]].GetOutput("surface"))
    if graph.data["volume"]:
        material.CreateOutput("moonray:volume", Sdf.ValueTypeNames.Token).ConnectToSource(shaders[graph.data["volume"]].GetOutput("out"))
    displacement = graph.data["displacement"]
    if displacement and (not preview or graph.data["preview"]["displacement_enabled"]):
        material.CreateOutput("moonray:displacement", Sdf.ValueTypeNames.Float3).ConnectToSource(shaders[displacement].GetOutput("out"))
    if preview:
        create_preview_sphere(stage, material, graph.data["preview"])
        ground = UsdGeom.Mesh.Define(stage, "/Preview/Ground")
        ground.CreatePointsAttr([(-200, -1.02, -200), (-200, -1.02, 200), (200, -1.02, 200), (200, -1.02, -200)])
        ground.CreateFaceVertexCountsAttr([4])
        ground.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        ground.CreateSubdivisionSchemeAttr("none")
        floor_material = UsdShade.Material.Define(stage, "/Preview/FloorMaterial")
        floor_shader = UsdShade.Shader.Define(stage, "/Preview/FloorMaterial/Shader")
        floor_shader.CreateIdAttr("DwaBaseMaterial")
        floor_shader.CreateInput("albedo", Sdf.ValueTypeNames.Color3f).Set((0.085, 0.10, 0.12))
        floor_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.7)
        floor_shader.CreateOutput("surface", Sdf.ValueTypeNames.Color4f)
        floor_material.CreateOutput("moonray:surface", Sdf.ValueTypeNames.Color4f).ConnectToSource(floor_shader.GetOutput("surface"))
        UsdShade.MaterialBindingAPI.Apply(ground.GetPrim()).Bind(floor_material)
        camera = UsdGeom.Camera.Define(stage, "/Preview/Camera")
        matrix = Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 0.55, 4.8), Gf.Vec3d(0, -0.08, 0), Gf.Vec3d(0, 1, 0)).GetInverse()
        camera.AddTransformOp().Set(matrix)
        camera.CreateFocalLengthAttr(50)
        camera.CreateHorizontalApertureAttr(30)
        camera.CreateVerticalApertureAttr(30)
        for name, position, intensity, color, size in [
            ("Key", (-3, 4, 3), 6, (1.0, 0.92, 0.83), 3),
            ("Fill", (3, 1, 2), 2, (0.75, 0.85, 1.0), 2.5),
            ("Rim", (0.5, 3, -2), 7.5, (0.85, 0.95, 1.0), 2)]:
            light = UsdLux.RectLight.Define(stage, "/Preview/" + name)
            light.CreateIntensityAttr(intensity)
            light.CreateColorAttr(tuple(c * graph.data["preview"]["studio_light_level"] for c in color))
            light.CreateWidthAttr(size)
            light.CreateHeightAttr(size)
            light.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(*position), Gf.Vec3d(0), Gf.Vec3d(0, 1, 0)).GetInverse())
        dome = UsdLux.DomeLight.Define(stage, "/Preview/Ambient")
        dome.CreateIntensityAttr(0.12)
        # A positive-intensity black dome suppresses the renderer's fallback
        # light when the studio rig is dimmed to zero for emission previews.
        dome.CreateColorAttr(tuple(c * graph.data["preview"]["studio_light_level"] for c in (0.65, 0.73, 0.85)))
    if path is not None:
        stage.GetRootLayer().Save()
    return stage


def run_native_renderer(command, progress_step=None, progress_range=None):
    # MoonRay can report shader errors yet return success; do not accept those previews.
    errors = []
    progress = RenderProgress(progress_step) if progress_step is not None else None
    def report(messages):
        for message in messages:
            if progress_range is not None:
                percent = int(message.removeprefix("Render progress: ").removesuffix("%"))
                start, end = progress_range
                message = f"Render progress: {start + (end-start)*percent//100}%"
            print(message, flush=True)
    if progress:
        command = list(command)
        if Path(command[0]).name == "moonray":
            command += ["-info"]
        # Native stdout is otherwise block-buffered when connected to a pipe.
        command = ["stdbuf", "-oL", "-eL", *command]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
        for line in process.stdout:
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if clean.startswith(("Error:", "ERROR:", "FATAL:")):
                errors.append(clean)
            messages = progress.consume(line) if progress else None
            if messages is None:
                print(line, end="", flush=True)
            else:
                report(messages)
        status = process.wait()
    if status:
        raise subprocess.CalledProcessError(status, command)
    if errors:
        raise GraphError("MoonRay reported a render error: " + errors[0])
    if progress:
        report(progress.finish())


def preview_integrator_args(graph):
    """Use the delegate's namespaced settings when translating a USD preview."""
    from .usd_render_config import SCENE_PREFIX, native_settings
    from .render_view_config import final_setting_specs
    preview = graph.data["preview"]
    values = {SCENE_PREFIX + key: preview[key] for key in (*RAY_DEPTHS, *LOBE_SAMPLES, *LIGHT_SAMPLING, "shadow_terminator_fix")}
    values.update({SCENE_PREFIX + "pixel_samples": preview["samples"],
                   SCENE_PREFIX + "enable_displacement": preview["displacement_enabled"]})
    settings = native_settings(values, final_setting_specs())
    arguments = [arg for key, (value, kind) in settings.items()
                 for arg in ("-set", key, str(value).lower() if kind == "bool" else str(value))]
    return [*arguments, "-set", SCENE_PREFIX + "pixel_filter_width", "2"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["render", "export", "export-rdl", "copy-preview", "save-preview", "moonray-gui", "export-rdl-gui"])
    parser.add_argument("graph", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--base-dir", type=Path)
    parser.add_argument("--mode", choices=["vectorized", "xpu"], default="vectorized")
    parser.add_argument("--progress-step", type=int, choices=PROGRESS_STEPS, default=DEFAULT_PROGRESS_STEP)
    parser.add_argument("--region", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument("--region-base", type=Path)
    parser.add_argument("--region-base-image", type=Path)
    args = parser.parse_args()
    region = validate_region(args.region, args.size, args.size)
    region_args = ["-sub_viewport", *map(str, native_region(region, args.size, args.size))] if region else []
    if args.action in ("moonray-gui", "export-rdl-gui", "export-rdl"):
        # This graph is a worker snapshot; relative assets belong to the original
        # material document, not the temporary directory containing this JSON.
        from .moonray_gui import export_material_scene, render_material_scene
        graph = Graph(Catalog(), json.loads(args.graph.read_text()))
        if args.action == "moonray-gui":
            render_material_scene(graph, args.output, args.base_dir or args.graph.parent, args.size, args.mode)
        else:
            export_material_scene(graph, args.output, args.base_dir or args.graph.parent, args.size, args.mode,
                                  launch=args.action == "export-rdl-gui")
        return
    # Snapshot JSON lives in a job directory; assets belong to the source graph.
    from .moonray_gui import scene_snapshot
    graph = scene_snapshot(Graph(Catalog(), json.loads(args.graph.read_text())), args.base_dir or args.graph.parent)
    graph.validate(require_surface=True)
    if args.action == "save-preview":
        from .preview_scene import save_preview_scene
        save_preview_scene(graph, args.output, args.base_dir or args.graph.parent, args.size)
        print(f"Saved preview scene to {args.output}", flush=True)
        return
    if args.action == "copy-preview":
        from .preview_scene import copy_preview_scene
        copy_preview_scene(graph, args.output, args.base_dir or args.graph.parent, args.size)
        print(f"Copied preview scene to {args.output}", flush=True)
        return
    if args.action == "export":
        from .usd_materials import material_graph
        graph = material_graph(graph.data, args.base_dir or args.graph.parent)
        # Author into a sibling temporary layer, then atomically replace the target.
        import tempfile
        with tempfile.TemporaryDirectory(dir=args.output.parent) as temp:
            path = Path(temp) / args.output.name
            create_stage(graph, path)
            path.replace(args.output)
        print(f"Exported {args.output}", flush=True)
        return
    if graph.data["aov"] and not graph.data["display"] and graph.value(graph.node(graph.data["aov"]), "output_type") == "deep":
        raise GraphError("Deep images cannot be displayed in the preview. Select a flat output, or export the RDL scene to render deep images externally.")
    args.output.mkdir(parents=True, exist_ok=True)
    scene, exr, png = [args.output / name for name in ("scene.usda", "preview.exr", "preview.png")]
    from .native import create_scene, preview_geometry
    native = (bool(graph.data["volume"]) or preview_geometry(graph) not in {"sphere"} or graph.data["preview"]["render_view"] != "beauty"
              or any(graph.catalog.definition(n["shader"])["type"] not in SHADER_TYPES for n in graph.data["nodes"])
              or any(c.get("output", "out") != "out" for c in graph.data["connections"]))
    if native:
        selected = create_scene(graph, args.output / "scene.rdla", args.output, args.size)
        print("Starting Rendering · native MoonRay scene", flush=True)
        run_native_renderer(["moonray", "-in", str(args.output / "scene.rdla"), "-out", str(exr), "-exec_mode", args.mode, *region_args],
                            progress_step=args.progress_step)
        # -out overrides SceneVariables' beauty path, not named RenderOutputs.
        exr = selected if selected.name != "beauty.exr" else exr
        from .render_image import convert_preview
        view = graph.data["preview"]["render_view"]
        if graph.data["display"]:
            view = "beauty"
        elif graph.data["aov"]:
            output_node = graph.node(graph.data["aov"])
            result = graph.value(output_node, "result")
            view = "depth" if result == 2 else "beauty"
            if result == 3:
                view = {1: "normal", 2: "normal", 3: "uv", 11: "depth"}.get(graph.value(output_node, "state_variable"), "raw")
            elif result == 7:
                aov = graph.value(output_node, "material_aov")
                view = "albedo" if aov in {"albedo", "color"} else "roughness" if aov == "roughness" else "raw"
        merge_region(exr, args.region_base, region, args.size, args.size)
        convert_preview(exr, png, view)
    else:
        create_stage(graph, scene, preview=True)
        print("Preparing material scene for MoonRay…", flush=True)
        # Preserve the Hydra scene translation, then use the same renderer as
        # final renders so live percentage reporting is available for all previews.
        rdl = args.output / "scene.rdla"
        run_native_renderer(["hd_usd2rdl", "-in", str(scene), "-out", str(rdl), "-camera", "Preview/Camera",
                    "-size", str(args.size), str(args.size), "-set", "executionMode", args.mode,
                    *preview_integrator_args(graph)])
        print("Starting Rendering · MoonRay material", flush=True)
        run_native_renderer(["moonray", "-in", str(rdl), "-out", str(exr), "-exec_mode", args.mode, *region_args],
                            progress_step=args.progress_step)
        merge_region(exr, args.region_base, region, args.size, args.size)
        subprocess.run(["oiiotool", str(exr), "--ch", "R,G,B", "--colorconvert", "linear", "sRGB", "-d", "uint8", "-o", str(png)], check=True)
    preserve_display_region(png, args.region_base_image, region)
    # Retain the source of the displayed image, including a selected AOV or display filter.
    if exr != args.output / "preview.exr":
        exr.replace(args.output / "preview.exr")
    print(f"Preview ready: {png}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
