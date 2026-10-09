"""Final-render options, independent of the Qt and renderer runtimes."""
import json
import math
from decimal import Decimal
from pathlib import Path

from .model import CATALOG_PATH, RAY_DEPTHS
from .usd_render_config import SCENE_PREFIX, setting_specs, validate_settings
from .aovs import preset, validate_outputs
from .geometry_settings import SHADOW_TERMINATOR_OPTIONS
from .render_progress import DEFAULT_PROGRESS_STEP, validate_progress_step
from .render_region import validate_region
from .usd_purposes import normalize_purposes

PASSES = {"beauty": {"result": 0}, "alpha": {"result": 1},
          "albedo": {"result": 7, "material_aov": "albedo"},
          "roughness": {"result": 7, "material_aov": "roughness"},
          "normal": {"result": 3, "state_variable": 2}, "depth": {"result": 2}}
OBJECT_SETTINGS = {"camera": ("Camera", "CAMERA"), "dicing_camera": ("Dicing camera", "CAMERA"),
                   "layer": ("Layer", "LAYER"), "exr_header_attributes": ("EXR header attributes", "METADATA")}


def frame_label(frame):
    if type(frame) not in (int, float) or not math.isfinite(frame):
        raise ValueError("Frame must be a finite number.")
    return "0" if frame == 0 else format(Decimal(str(frame)).normalize(), "f")


def output_base(path):
    path = Path(path).expanduser().absolute()
    return path.with_suffix("") if path.suffix.lower() == ".exr" else path


def frame_output_path(path, frame):
    base = output_base(path)
    return base.with_name(base.name + "." + frame_label(frame) + ".EXR")


def render_frames(data):
    if data.get("frame_mode", "current") == "current":
        return (data["frame"],)
    if data["frame_mode"] != "range":
        raise ValueError("Choose Current frame or Frame range.")
    start, end = data.get("range_start"), data.get("range_end")
    if any(type(value) is not int or not -(2 ** 31) <= value < 2 ** 31 for value in (start, end)):
        raise ValueError("Frame range endpoints must be 32-bit whole numbers.")
    if start > end:
        raise ValueError("Start frame must not be after end frame.")
    return range(start, end + 1)


def final_setting_specs():
    scene = json.loads((CATALOG_PATH / "SceneVariables.json").read_text())["scene_classes"]["SceneVariables"]
    curated = {spec["key"]: spec for spec in setting_specs({})}
    grouping = scene["grouping"]
    groups = {name: group for group, names in grouping["groups"].items() for name in names}
    managed = {
        "camera": "Selected source camera / Camera field", "layer": "Source scene material and light assignments",
        "dicing_camera": "Render camera (automatic)", "exr_header_attributes": "Source scene Metadata object",
        "primary_aov": "AOV Workflow outputs", "image_width": "Width field", "image_height": "Height field",
        "sub_viewport": "Red region selected in the render image",
        "frame": "Current frame / frame range", "min_frame": "Frame range start", "max_frame": "Frame range end",
        "output_file": "EXR output destination", "tmp_dir": "Isolated render working directory",
        "machine_id": "Local renderer process", "num_machines": "Local renderer process",
        "resume_render": "RenderView starts new renders; resume requires an external MoonRay job",
        "checkpoint_active": "Live preview / background render", "checkpoint_mode": "Timed live checkpoints",
        "checkpoint_interval": "Live image update interval", "checkpoint_start_sample": "First live checkpoint",
        "checkpoint_overwrite": "Atomic live checkpoint refresh", "checkpoint_bg_write": "Live checkpoint writer",
        "two_stage_output": "Atomic output publication", "checkpoint_time_cap": "RenderView job lifecycle",
        "checkpoint_sample_cap": "RenderView job lifecycle",
    }
    specs = []
    for name, attr in scene["attributes"].items():
        key = SCENE_PREFIX + name
        kind = {"Int": "int", "Float": "float", "Bool": "bool", "String": "string",
                "Rgb": "vector", "Rgba": "vector", "Vec4f": "vector", "SceneObject*": "string"}.get(attr["attrType"], "json")
        group = groups.get(name, "Debug" if name.startswith("fatal_") else "Sampling" if "sampling" in name or "adaptive" in name else "Advanced")
        if name in ("enable_displacement", "enable_max_geometry_resolution", "max_geometry_resolution", "shadow_terminator_fix"):
            group = "Displacement and subdivision"
        elif name in RAY_DEPTHS:
            group = "Ray depth"
        elif name in ("light_samples", "light_sampling_mode", "light_sampling_quality"):
            group = "Light sampling"
        elif name in ("enable_motion_blur", "motion_steps", "slerp_xforms", "fps"):
            group = "Motion blur"
        elif name == "scene_scale":
            group = "Scene scale"
        spec = dict(key=key, group=group, label=attr.get("metadata", {}).get("label", name.replace("_", " ")).capitalize(),
                    type=kind, rdl_type=attr["attrType"], default=attr["default"],
                    minimum=-(2**31) if kind == "int" else -1e12 if kind == "float" else None,
                    maximum=2**31-1 if kind == "int" else 1e12 if kind == "float" else None,
                    help=attr.get("metadata", {}).get("comment", "").strip() + "\n" + name)
        if key in curated:
            spec.update({field: value for field, value in curated[key].items() if field not in ("group", "help")})
        if name in RAY_DEPTHS:
            spec["default"] = RAY_DEPTHS[name][1]
        if name == "enable_motion_blur":
            spec["help"] += "\nUSD workflow: select an authored USD camera and set shutter:open / shutter:close in Prim Properties (for example -0.25 / 0.25 frames). Moving objects or cameras need authored time samples or supported velocity data. A static material preview has no motion to blur."
        if name == "motion_steps":
            spec["help"] += "\nEnter one or two finite frame offsets only: [0] for a single sample or [-0.25, 0.25] for two samples. Empty lists and more than two steps are unsupported by MoonRay. For USD renders, match these offsets to the selected camera's shutter:open / shutter:close. These describe the exported motion samples, not extra blur applied to a still image."
        if "enum" in attr:
            spec["options"] = sorted(attr["enum"].items(), key=lambda item: item[1])
        if name == "shadow_terminator_fix":
            spec["options"] = SHADOW_TERMINATOR_OPTIONS
        if name == "max_geometry_resolution":
            spec["minimum"] = 1
            spec["help"] += "\nProcedural-dependent cap; RdlMeshGeometry uses its per-edge mesh resolution instead. Use Override mesh tessellation for Hydra meshes."
        if name in managed:
            spec["read_only"] = managed[name]
        if name in OBJECT_SETTINGS:
            spec["object_picker"] = name
            spec["help"] += "\nUse … to choose a compatible object from the current render source."
        specs.append(spec)
    order = ["Displacement and subdivision", "Sampling", "Light sampling", "Ray depth", "Motion blur",
             *["Scene scale" if group == "Motion and Scale" else group for group in grouping["order"]], "Advanced"]
    motion_order = [SCENE_PREFIX + name for name in ("enable_motion_blur", "motion_steps", "fps", "slerp_xforms")]
    specs.sort(key=lambda spec: (order.index(spec["group"]), spec["key"] == SCENE_PREFIX + "max_depth",
                                motion_order.index(spec["key"]) if spec["group"] == "Motion blur" else
                                scene["attributes"][spec["key"].removeprefix(SCENE_PREFIX)].get("order", 0)))
    return specs + [spec for spec in curated.values() if not spec["key"].startswith(SCENE_PREFIX)]


def validate_render_request(data):
    renderer = data.get("renderer", "moonray")
    if renderer not in ("moonray", "ovrtx"):
        raise ValueError("Choose MoonRay or ovRTX for rendering.")
    if renderer == "ovrtx" and data.get("source") == "material":
        raise ValueError("ovRTX renders USD Viewer scenes or USD files. Use MoonRay for the active material studio.")
    for key in ("width", "height"):
        if type(data.get(key)) is not int or not 16 <= data[key] <= 8192:
            raise ValueError("Image dimensions must be between 16 and 8192 pixels.")
    if type(data.get("frame")) not in (int, float) or not math.isfinite(data["frame"]):
        raise ValueError("Frame must be a finite number.")
    render_frames(data)
    if data.get("mode") not in ("vectorized", "scalar", "xpu"):
        raise ValueError("Choose CPU or GPU/XPU execution.")
    purposes = normalize_purposes(data.get("purpose"))
    if type(data.get("subdivision")) is not int or not 0 <= data["subdivision"] <= 8:
        raise ValueError("Subdivision level must be between 0 and 8.")
    if not isinstance(data.get("camera"), str):
        raise ValueError("Camera must be a USD prim path.")
    if "outputs" in data:
        outputs = validate_outputs(data["outputs"])
    else:
        # Accept requests saved by earlier versions and existing batch scripts.
        if not isinstance(data.get("passes"), list) or not data["passes"] or any(p not in PASSES for p in data["passes"]):
            raise ValueError("Choose at least one render output.")
        outputs = [preset(key) for key in dict.fromkeys(data["passes"])]
    specs = final_setting_specs()
    defaults = {spec["key"]: spec["default"] for spec in specs if not spec.get("read_only")}
    data = dict(data)
    data["renderer"] = renderer
    data["purpose"] = purposes
    data["progress_step"] = validate_progress_step(data.get("progress_step", DEFAULT_PROGRESS_STEP))
    data["render_region"] = validate_region(data.get("render_region"), data["width"], data["height"])
    objects = data.get("scene_objects", {})
    if not isinstance(objects, dict) or any(key not in OBJECT_SETTINGS or not isinstance(name, str) or not name or "\x00" in name
                                           for key, name in objects.items()):
        raise ValueError("Invalid render scene object selection.")
    data["scene_objects"] = dict(objects)
    data["outputs"] = outputs
    data.setdefault("match_subdivision", True)
    data.setdefault("force_polygon", False)
    data.setdefault("use_default_light", True)
    if type(data["use_default_light"]) is not bool:
        raise ValueError("Use default light must be enabled or disabled.")
    if type(data["match_subdivision"]) is not bool or type(data["force_polygon"]) is not bool:
        raise ValueError("Subdivision matching and Force polygon meshes must be enabled or disabled.")
    data.setdefault("live_updates", True)
    data.setdefault("background", False)
    if type(data["background"]) is not bool:
        raise ValueError("Background rendering must be enabled or disabled.")
    if data["background"]:
        data["live_updates"] = False
    data.setdefault("update_interval", 6)
    if type(data["live_updates"]) is not bool:
        raise ValueError("Live updates must be enabled or disabled.")
    if type(data["update_interval"]) is not int or not 6 <= data["update_interval"] <= 3600:
        raise ValueError("Live update interval must be between 6 and 3600 seconds.")
    if renderer == "ovrtx":
        from .ovrtx_settings import validate_final_settings
        from .ovrtx_outputs import DEFAULT_OUTPUTS, validate_outputs as validate_ovrtx_outputs
        data["ovrtx_settings"] = validate_final_settings(data.get("ovrtx_settings", {}))
        data["ovrtx_outputs"] = validate_ovrtx_outputs(data.get("ovrtx_outputs", DEFAULT_OUTPUTS), data["ovrtx_settings"]["mode"])
        data["outputs"] = [preset("beauty"), preset("alpha")]
        data["settings"] = {}
        data["scene_objects"] = {}
        # A PT step completes its sample budget before publishing; RTPT can
        # provide intermediate frames for the existing checkpoint display.
        if data["ovrtx_settings"]["mode"] == "PathTracing":
            data["live_updates"] = False
    else:
        data["settings"] = validate_settings(data.get("settings", {}), defaults, specs)
    if renderer == "moonray" and data["render_region"] and data["settings"].get(SCENE_PREFIX + "res", 1) != 1:
        raise ValueError("Render regions require Res 1. Set Res to 1, clear the region, and render a full frame first.")
    return data
