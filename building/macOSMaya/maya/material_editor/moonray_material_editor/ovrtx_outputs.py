"""Camera image outputs supported by the installed ovRTX API, independent of Qt."""

OUTPUTS = {
    "HdrColor": dict(label="Beauty (HDR + alpha)", channels="RGBA", display="srgb",
                     help="Scene-linear HDR beauty. Always included for RenderView and frame comparison."),
    "LdrColor": dict(label="Beauty (display color)", channels="RGBA", display="raw",
                     help="Tone-mapped sRGB beauty, normalized from 8-bit values. Do not apply another sRGB display transform."),
    "NormalSD": dict(label="World normals", channels="XYZA", display="signed", rtpt=True,
                     help="World-space surface normals. Signed values are retained in the EXR."),
    "DistanceToCameraSD": dict(label="Distance to camera", channels="Z", display="normalize", rtpt=True,
                     help="Euclidean distance from the camera origin to the surface, in meters."),
    "DistanceToImagePlaneSD": dict(label="Distance to image plane", channels="Z", display="normalize", rtpt=True,
                     help="Perpendicular distance from the image plane to the surface, in meters."),
    "DiffuseAlbedoSD": dict(label="Diffuse albedo", channels="RGBA", display="raw", rtpt=True,
                     help="Base color without lighting, normalized from the renderer's 8-bit output."),
    "Camera3dPositionSD": dict(label="Camera-space position", channels="XYZA", display="signed", rtpt=True,
                     help="Camera-space surface positions in scene units. Original values are retained in the EXR."),
}
DEFAULT_OUTPUTS = ["HdrColor"]


def validate_outputs(names, mode=None):
    if (not isinstance(names, list) or not names or any(not isinstance(name, str) or name not in OUTPUTS for name in names)
            or len(set(names)) != len(names) or "HdrColor" not in names):
        raise ValueError("Choose valid ovRTX outputs including HDR beauty.")
    if mode == "PathTracing" and any(OUTPUTS[name].get("rtpt") for name in names):
        raise ValueError("Selected ovRTX passes require real-time path tracing.")
    return [name for name in OUTPUTS if name in names]


def active_outputs(names, mode):
    return [name for name in validate_outputs(names) if mode != "PathTracing" or not OUTPUTS[name].get("rtpt")]


def channel_names(name):
    return [channel if name == "HdrColor" else name + "." + channel for channel in OUTPUTS[name]["channels"]]


def apply_display_hints(result):
    """Keep normalized display color and data passes out of the beauty transform."""
    for view in result["views"]:
        names = [result["parts"][view["part"]]["channels"][index] for index in view["channels"]]
        for name, spec in OUTPUTS.items():
            expected = channel_names(name)
            main = expected[:3] if len(expected) == 4 else expected
            if names == main:
                view["display"] = spec["display"]
    return result
