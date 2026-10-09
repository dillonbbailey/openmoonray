"""Typed MoonRay viewport settings shared by the UI and isolated worker."""
import json
import math
import os

from .model import CATALOG_PATH, validate_value
from .geometry_settings import GEOMETRY_PREFIX, SHADOW_TERMINATOR_OPTIONS, geometry_specs

MOONRAY = "HdMoonrayRendererPlugin"
SCENE_PREFIX = "moonray:sceneVariable:"

# Names and defaults come from the installed renderer. These are the controls
# relevant to an interactive local viewport, excluding file and remote outputs.
SCENE_CONTROLS = [
    ("Sampling", "sampling_mode", "Sampling mode", 0, 2),
    ("Sampling", "pixel_samples", "Pixel samples (squared)", 1, 64),
    ("Sampling", "min_adaptive_samples", "Minimum samples", 1, 1048576),
    ("Sampling", "max_adaptive_samples", "Maximum samples", 1, 1048576),
    ("Sampling", "target_adaptive_error", "Target error", 0.01, 1000.0),
    ("Light sampling", "light_samples", "Light samples (squared)", 1, 64),
    ("Sampling", "bsdf_samples", "BSDF samples (squared)", 1, 64),
    ("Sampling", "bssrdf_samples", "SSS samples (squared)", 1, 64),
    ("Ray depth", "max_diffuse_depth", "Diffuse depth", 0, 64),
    ("Ray depth", "max_glossy_depth", "Glossy depth", 0, 64),
    ("Ray depth", "max_mirror_depth", "Mirror depth", 0, 64),
    ("Ray depth", "max_hair_depth", "Hair depth", 0, 64),
    ("Ray depth", "max_volume_depth", "Volume depth", 0, 64),
    ("Ray depth", "max_presence_depth", "Presence depth", 0, 64),
    ("Ray depth", "max_depth", "Total depth", 0, 64),
    ("Displacement and subdivision", "enable_displacement", "Enable displacement", None, None),
    ("Displacement and subdivision", "shadow_terminator_fix", "Shadow terminator correction", 0, 4),
    ("Image and geometry", "enable_dof", "Depth of field", None, None),
    ("Image and geometry", "sample_clamping_value", "Sample clamp (0 = off)", 0.0, 10000.0),
    ("Volumes", "volume_quality", "Volume quality", 0.01, 100.0),
    ("Volumes", "volume_shadow_quality", "Volume shadow quality", 0.01, 100.0),
    ("Volumes", "volume_illumination_samples", "Illumination samples", 0, 128),
    ("Volumes", "volume_indirect_samples", "Indirect samples", 0, 128),
]
DELEGATE_CONTROLS = [
    ("Execution", "executionMode", "Execution mode", "string", None, None,
     "Changing execution mode restarts the MoonRay rendering session."),
    ("Execution", "maxFps", "Maximum update FPS", "float", 1.0, 120.0,
     "Maximum progressive image update rate. Changing this restarts the rendering session."),
    ("Execution", "localReservedCores", "Reserved CPU cores", "int", 0, 1024,
     "CPU cores left free by the local render. Changing this restarts the rendering session."),
    ("Denoising", "denoiseAlbedoGuiding", "Use albedo guide", "bool", None, None, "Use albedo to guide denoising."),
    ("Denoising", "denoiseNormalGuiding", "Use normal guide", "bool", None, None, "Use normals to guide denoising."),
    ("Motion blur", "enableMotionBlur", "Enable motion blur", "bool", None, None,
     "Use animation samples authored in the stage. Select a USD camera and set its shutter:open and shutter:close in Prim Properties; a zero-length shutter stays sharp."),
    ("Image and geometry", "doubleSided", "Force double sided", "bool", None, None, "Render surfaces as double sided."),
    ("Image and geometry", "disableLighting", "Disable lighting", "bool", None, None, "Show unlit material shading."),
    ("Displacement and subdivision", "forcePolygon", "Force polygon meshes", "bool", None, None, "Disable subdivision surfaces while keeping each mesh's tessellation and displacement settings."),
]


def setting_specs(delegate_defaults):
    attributes = json.loads((CATALOG_PATH / "SceneVariables.json").read_text())["scene_classes"]["SceneVariables"]["attributes"]
    specs = []
    for group, key, label, kind, low, high, tip in DELEGATE_CONTROLS:
        if key not in delegate_defaults:
            continue
        spec = dict(group=group, key=key, label=label, type=kind, minimum=low, maximum=high,
                    default=delegate_defaults[key], help=tip)
        if key == "localReservedCores":
            spec["maximum"] = max(0, len(os.sched_getaffinity(0)) - 1)
        if key == "executionMode":
            spec["options"] = [("Automatic", "auto"), ("CPU · Vectorized", "vectorized"),
                               ("CPU · Scalar", "scalar"), ("GPU · XPU", "xpu")]
        specs.append(spec)
    if "enableDenoise" in delegate_defaults and "enableOIDN" in delegate_defaults:
        default = "off" if not delegate_defaults["enableDenoise"] else "oidn" if delegate_defaults["enableOIDN"] else "optix"
        specs.append(dict(group="Denoising", key="denoiser", label="Denoiser", type="string", default=default,
                          options=[("Off", "off"), ("Open Image Denoise", "oidn"), ("OptiX", "optix")],
                          help="Denoise the progressive viewport image using the selected engine."))
    for group, name, label, low, high in SCENE_CONTROLS:
        if name not in attributes:
            continue
        attr = attributes[name]
        spec = dict(group=group, key=SCENE_PREFIX + name, label=label,
                    type={"Int": "int", "Float": "float", "Bool": "bool"}[attr["attrType"]],
                    minimum=low, maximum=high, default=attr["default"], help=attr.get("metadata", {}).get("comment", ""))
        if "enum" in attr:
            spec["options"] = sorted(((label.capitalize(), value) for label, value in attr["enum"].items()), key=lambda x: x[1])
            spec["native_enum"] = attr["enum"]
        if name == "shadow_terminator_fix":
            spec["options"] = SHADOW_TERMINATOR_OPTIONS
        specs.append(spec)
    return specs + geometry_specs()


def validate_settings(changes, current, specs):
    if not isinstance(changes, dict):
        raise ValueError("Render settings must be an object.")
    known = {spec["key"]: spec for spec in specs}
    candidate = dict(current)
    for key, value in changes.items():
        if key not in known:
            raise ValueError("Unsupported MoonRay viewport setting: " + key)
        spec = known[key]
        if spec.get("read_only"):
            raise ValueError(spec["label"] + " is managed by Lunatic: " + spec["read_only"])
        kind = spec["type"]
        if kind in ("json", "vector"):
            validate_value({"attrType": spec["rdl_type"]}, value)
            candidate[key] = value
            continue
        valid = (kind == "bool" and type(value) is bool or kind == "int" and type(value) is int or
                 kind == "float" and type(value) in (int, float) and math.isfinite(value) or
                 kind == "string" and isinstance(value, str))
        if not valid:
            raise ValueError(f"{spec['label']} requires a {kind} value.")
        if "options" in spec and value not in [option[1] for option in spec["options"]]:
            raise ValueError("Invalid option for " + spec["label"])
        if spec.get("minimum") is not None and not spec["minimum"] <= value <= spec["maximum"]:
            raise ValueError(f"{spec['label']} must be between {spec['minimum']} and {spec['maximum']}.")
        candidate[key] = float(value) if kind == "float" else value
    if candidate.get(SCENE_PREFIX + "min_adaptive_samples", 1) > candidate.get(SCENE_PREFIX + "max_adaptive_samples", 1048576):
        raise ValueError("Minimum adaptive samples must not exceed maximum adaptive samples.")
    motion_key = SCENE_PREFIX + "motion_steps"
    if motion_key in candidate:
        steps = candidate[motion_key]
        if (not isinstance(steps, list) or len(steps) not in (1, 2)
                or any(type(step) not in (int, float) or not math.isfinite(step) for step in steps)):
            raise ValueError("Motion steps requires one or two finite frame offsets: use [0] for a single sample "
                             "or two offsets such as [-0.25, 0.25] for motion blur. MoonRay cannot render "
                             "an empty list or more than two motion steps.")
    return candidate


def native_settings(values, specs):
    known = {spec["key"]: spec for spec in specs}
    result = {}
    for key, value in values.items():
        if key.startswith(GEOMETRY_PREFIX):
            continue
        elif key == "denoiser":
            result["enableDenoise"] = (value != "off", "bool")
            result["enableOIDN"] = (value == "oidn", "bool")
        elif "native_enum" in known[key]:
            # hdMoonray interprets integers as enum positions, not RDL values
            # (adaptive is value 2 but position 1). Named values are unambiguous.
            name = next(name for name, number in known[key]["native_enum"].items() if number == value)
            result[key] = (name, "string")
        else:
            result[key] = (value, known[key]["type"])
    return result
