"""Final MoonRay renders with checkpoint images, outside the Qt6 process."""
import json
import os
from pathlib import Path
import shutil
import sys

from .exr_image import display_exr, inspect_exr
from .model import Catalog, Graph
from .native import create_scene, quote, value
from .render_view_config import validate_render_request
from .aovs import apply_display_hints, output_properties
from .usd_render_config import SCENE_PREFIX
from .worker import run_native_renderer
from .render_region import merge_region, native_region, preserve_display_region
from .geometry_settings import geometry_values, create_geometry_layer, update_geometry_layer


def default_light_override(scene, enabled):
    if not enabled:
        with scene.open() as file:
            if any(line.startswith('EnvLight("defaultLight") {') for line in file):
                return '\nEnvLight("defaultLight") { ["on"] = false }\n'
    return ""


def preserve_region_display(result, data, metadata):
    previous = metadata.get("region_display") or {}
    if (previous.get("path") and metadata.get("render_region")
            and all(data.get(key, default) == previous.get(key) for key, default in
                    (("selection", {}), ("display", "auto"), ("exposure", 0)))):
        preserve_display_region(result["image"], previous["path"], metadata["render_region"],
                                result["display_info"]["stride"])


def prepare_scene(data, directory):
    """Translate the chosen source without launching a render."""
    scene = directory / "scene.rdla"
    if data["source"] == "material":
        graph = Graph(Catalog(), json.loads(Path(data["input"]).read_text()))
        geometry = geometry_values(data["settings"])
        graph.data["preview"]["displacement_enabled"] = data["settings"][SCENE_PREFIX + "enable_displacement"]
        if geometry["override"]:
            graph.data["preview"].update({key: geometry[key] for key in ("mesh_resolution", "adaptive_error")})
            if geometry["scheme"] != "authored":
                graph.data["preview"]["subdivision_scheme"] = geometry["scheme"]
            for node in graph.data["nodes"]:
                if node["shader"] == "RdlMeshGeometry":
                    node["values"].update({key: float(geometry[key]) for key in ("mesh_resolution", "adaptive_error")})
                    if geometry["scheme"] != "authored":
                        node["values"].update(is_subd=geometry["scheme"] != "none", subd_scheme=0 if geometry["scheme"] == "bilinear" else 1)
        # Match texture resolution to the original graph file's folder.
        for node in graph.data["nodes"]:
            for name, attr in graph.catalog.attributes(node["shader"]).items():
                path = node["values"].get(name)
                if attr.get("filename") and isinstance(path, str) and path and "://" not in path:
                    target = Path(os.path.expandvars(path)).expanduser()
                    if not target.is_absolute():
                        target = Path(data["base_dir"]) / target
                    node["values"][name] = str(target)
        vdb = graph.data["preview"]["vdb_file"]
        if vdb:
            target = Path(os.path.expandvars(vdb)).expanduser()
            graph.data["preview"]["vdb_file"] = str(target if target.is_absolute() else Path(data["base_dir"]) / target)
        create_scene(graph, scene, directory, data["width"])
    else:
        from pxr import Usd, UsdGeom
        from .usd_schemas import register_schemas
        register_schemas()
        stage = Usd.Stage.Open(data["input"])
        if not stage:
            raise ValueError("Could not open the USD scene.")
        camera_prim = stage.GetPrimAtPath(data["camera"]) if data["camera"] else None
        if data["camera"] and (not camera_prim or not camera_prim.IsA(UsdGeom.Camera)):
            raise ValueError("The camera path does not identify a USD camera: " + data["camera"])
        input_path = data["input"]
        geometry = geometry_values(data["settings"])
        if geometry["override"]:
            geometry_layer = create_geometry_layer(stage)
            update_geometry_layer(stage, geometry_layer, geometry)
            input_path = str(directory / "tessellation.usda")
            if not stage.Flatten().Export(input_path):
                raise ValueError("Could not prepare tessellation overrides.")
        command = ["hd_usd2rdl", "-in", input_path, "-out", str(scene),
                   "-size", str(data["width"]), str(data["height"]), "-time", str(data["frame"]),
                   "-refine-level", str(data["subdivision"]),
                   "-set", "forcePolygon", "true" if data["force_polygon"] else "false"]
        if data["camera"]:
            command += ["-camera", data["camera"].lstrip("/")]
        from .usd_purposes import normalize_purposes
        for purpose in normalize_purposes(data["purpose"]):
            command += ["-purpose", purpose]
        print("Preparing USD scene for final rendering…", flush=True)
        run_native_renderer(command)
        if not scene.is_file():
            raise ValueError("USD translation did not produce a renderer scene.")
    return scene


def prepare_render_scene(data, directory):
    """Translate the scene and apply RenderView settings without starting MoonRay."""
    scene = prepare_scene(data, directory)
    from .render_scene_objects import apply_scene_objects
    object_references = apply_scene_objects(data, scene, directory)
    from .render_textures import prepare_scene_textures
    prepare_scene_textures(scene, directory)
    output = directory / "render.exr"
    from .model import CATALOG_PATH
    attrs = json.loads((CATALOG_PATH / "SceneVariables.json").read_text())["scene_classes"]["SceneVariables"]["attributes"]
    settings = {key.removeprefix(SCENE_PREFIX): val for key, val in data["settings"].items() if key.startswith(SCENE_PREFIX)}
    settings.update(image_width=data["width"], image_height=data["height"], output_file=str(directory / "beauty.exr"))
    settings["sub_viewport"] = (native_region(data["render_region"], data["width"], data["height"])
                                if data["render_region"] else attrs["sub_viewport"]["default"])
    settings.update(checkpoint_active=data["live_updates"], checkpoint_mode=0,
                    checkpoint_interval=data["update_interval"] / 60.0, checkpoint_start_sample=1,
                    checkpoint_overwrite=True, checkpoint_bg_write=True, two_stage_output=True,
                    checkpoint_time_cap=0.0, checkpoint_sample_cap=0, tmp_dir=str(directory))
    lines = ["\n-- RenderView final-render settings\nSceneVariables {",
             *[f"    [{quote(key)}] = {value(attrs[key]['attrType'], val)}," for key, val in settings.items()], "}"]
    for row in data["outputs"]:
        if not row["enabled"]:
            continue
        props = dict(output_properties(row), file_name=str(output), compression=3,
                     checkpoint_file_name=str(directory / "live.exr"))
        lines.append(f"RenderOutput({quote('/RenderView/' + row['name'])}) {{")
        for key, val in props.items():
            lines.append(f"    [{quote(key)}] = {quote(val) if isinstance(val, str) else val},")
        if "exr_header_attributes" in object_references:
            lines.append('    ["exr_header_attributes"] = ' + object_references["exr_header_attributes"] + ',')
        lines.append("}")
    with scene.open("a") as file:
        if data["source"] != "material":
            file.write(default_light_override(scene, data["use_default_light"]))
        file.write("\n".join(lines) + "\n")
    return scene, output


def export_to_moonray_gui(data, directory):
    return export_scene_rdl(data, directory, launch=True)


def export_scene_rdl(data, directory, *, launch=False):
    from .moonray_gui import export_and_launch, export_scene
    data = validate_render_request(dict(data, live_updates=False, frame_mode="current", render_region=None))

    def prepare(assets):
        snapshot = dict(data)
        source = Path(snapshot["input"])
        if source.resolve().is_relative_to(directory.resolve()):
            # Viewer snapshots must outlive the temporary preparation job.
            persistent = assets / source.name
            shutil.copyfile(source, persistent)
            snapshot["input"] = str(persistent)
        return prepare_render_scene(snapshot, assets)[0]

    return (export_and_launch(data["export_path"], data["mode"], prepare) if launch else
            export_scene(data["export_path"], prepare))


def render(data, directory):
    data = validate_render_request(data)
    if data["renderer"] == "ovrtx":
        from .ovrtx_final import render as render_ovrtx
        return render_ovrtx(data, directory)
    scene, output = prepare_render_scene(data, directory)
    print("Starting final render…", flush=True)
    run_native_renderer(["moonray", "-in", str(scene), "-exec_mode", data["mode"]], progress_step=data["progress_step"])
    merge_region(output, data.get("region_base"), data["render_region"], data["width"], data["height"])
    metadata = apply_display_hints(inspect_exr(output), data["outputs"])
    metadata["render_region"] = data["render_region"]
    metadata["region_display"] = data.get("region_display")
    if not data["background"]:
        metadata["image"] = str(directory / "display.png")
        metadata["display_info"] = display_exr(output, metadata["image"], selection={}, metadata=metadata)
    return metadata


def main():
    if sys.argv[1] == "--probe":
        from .exr_image import PixelReader
        reader = PixelReader()
        for line in sys.stdin:
            request = {}
            try:
                request = json.loads(line)
                result = reader.sample(request["path"], request["x"], request["y"], request["channels"])
            except Exception as exc:
                result = dict(error=str(exc))
            print("EXR_PIXEL " + json.dumps(dict(result, serial=request.get("serial"))), flush=True)
        return
    request_path = Path(sys.argv[1])
    data = json.loads(request_path.read_text())
    directory = request_path.parent
    os.chdir(directory)
    action = data.pop("action")
    if action == "thumbnail":
        from .texture_thumbnail import make_thumbnail
        result = make_thumbnail(data["input"], directory / "thumbnail.png", data.get("raw", False), data.get("size", 256))
    elif action == "map_thumbnail":
        from .map_preview import render_map
        result = render_map(data, directory)
    elif action == "prepare_tx":
        from .tx_cache import prepare_texture
        result = dict(texture=prepare_texture(data["input"], cache=data["cache"]))
    elif action == "bake_texture":
        from .ramp_bake import bake_texture
        result = bake_texture(data, directory)
    elif action == "light_graph":
        from .light_graph import light_update
        result = light_update(Graph(Catalog(), data["graph"]), data["node"], data["textures"])
    elif action == "freeze":
        from pxr import Usd
        from .usd_schemas import register_schemas
        register_schemas()
        stage = Usd.Stage.Open(data["input"])
        if not stage:
            raise ValueError("Could not open the USD scene.")
        if data.get("renderer") == "ovrtx":
            from .ovrtx_scene import write_snapshot
            snapshot = write_snapshot(stage, directory / "frozen", purposes=data["purpose"],
                                      freeze=True, default_light=False)["scene"]
        else:
            snapshot = directory / "frozen.usdc"
            if not stage.Flatten().Export(str(snapshot)):
                raise ValueError("Could not freeze the USD scene for frame-range rendering.")
        result = dict(input=str(snapshot))
    elif action == "cameras":
        from .render_scene_objects import inspect_usd_cameras
        result = dict(objects=inspect_usd_cameras(data["input"]))
    elif action == "scene_objects":
        from .render_scene_objects import inspect_scene_objects
        result = inspect_scene_objects(data, directory)
    elif action == "render":
        result = render(data, directory)
    elif action == "export-gui":
        result = export_to_moonray_gui(data, directory)
    elif action == "export-rdl":
        result = export_scene_rdl(data, directory)
    elif action == "inspect":
        source = data["input"]
        if data.get("snapshot"):
            # MoonRay publishes using rename (two_stage_output). Copy through one
            # open descriptor so later checkpoints cannot change this inspection.
            source = str(directory / "snapshot.exr")
            with open(data["input"], "rb") as original, open(source, "wb") as snapshot:
                shutil.copyfileobj(original, snapshot)
            if data.get("render_region"):
                merge_region(source, data.get("region_base"), data["render_region"], data["width"], data["height"])
        result = apply_display_hints(inspect_exr(source), data.get("outputs", []))
        result["render_region"] = data.get("render_region")
        result["region_display"] = data.get("region_display")
        if data.get("snapshot") and result["region_display"] and result["region_display"].get("path"):
            previous = dict(result["region_display"])
            image = directory / "region_base.png"
            shutil.copyfile(previous["path"], image)
            previous["path"] = str(image)
            result["region_display"] = previous
        result["image"] = str(directory / "display.png")
        result["display_info"] = display_exr(source, result["image"], display=data.get("display", "auto"),
                                              exposure=data.get("exposure", 0), selection=data.get("selection", {}), metadata=result)
        preserve_region_display(result, data, result)
    elif action == "display":
        result = dict(image=str(directory / "display.png"))
        result["display_info"] = display_exr(data["input"], result["image"], display=data["display"],
                                              exposure=data["exposure"], selection=data["selection"], metadata=data["metadata"])
        preserve_region_display(result, data, data["metadata"])
    else:
        raise ValueError("Unknown RenderView operation.")
    (directory / "result.json").write_text(json.dumps(result))
    print("RenderView operation complete.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
