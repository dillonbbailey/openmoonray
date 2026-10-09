"""Validated viewport settings shared by Qt hosts and the optional RTX worker."""

SPECS = (
    dict(key="mode", label="Render mode", type="enum", default="RealTimePathTracing",
         options=(("Real-time path tracing", "RealTimePathTracing"), ("Path tracing", "PathTracing")),
         help="Real-time mode refines across frames. Path tracing accumulates its sample budget before returning an image."),
    dict(key="max_dimension", label="Max viewport dimension", type="int", default=1280, minimum=64, maximum=8192,
         help="Maximum rendered width or height in pixels. The viewport aspect ratio is preserved."),
    dict(key="accumulation_frames", label="Accumulation frames", type="int", default=32, minimum=1, maximum=4096,
         mode="RealTimePathTracing", help="Refinement frames after the camera, scene or settings change."),
    dict(key="samples_per_pixel", label="Samples per pixel", type="int", default=64, minimum=1, maximum=65536,
         mode="PathTracing", help="Maximum path-tracing samples per pixel. Higher values take longer to render."),
    dict(key="denoising", label="Denoising", type="bool", default=True, mode="PathTracing",
         help="Enable the path-tracing denoiser."),
    dict(key="firefly_filter", label="Firefly filter", type="bool", default=True,
         help="Filter unusually bright samples in the selected rendering mode."),
    dict(key="extra_bounces", label="Extra specular / transmission bounces", type="int", default=0, minimum=0, maximum=64,
         help="Additional specular and transmission bounces beyond the base bounce limit."),
    dict(key="max_bounces", label="Max bounces", type="int", default=3, minimum=0, maximum=64,
         help="Base ray-bounce limit for the selected rendering mode."),
)
DEFAULTS = {spec["key"]: spec["default"] for spec in SPECS}
FINAL_DEFAULTS = {key: ("PathTracing" if key == "mode" else value)
                  for key, value in DEFAULTS.items() if key != "max_dimension"}


def validate_final_settings(values):
    if not isinstance(values, dict) or set(values) - set(FINAL_DEFAULTS):
        raise ValueError("Invalid ovRTX final-render settings.")
    validated = validate_settings({**FINAL_DEFAULTS, **values})
    return {key: validated[key] for key in FINAL_DEFAULTS}


def validate_settings(values, current=None):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("Invalid ovRTX render settings.")
    result = {**DEFAULTS, **(current or {}), **values}
    for spec in SPECS:
        value = result[spec["key"]]
        valid = (type(value) is bool if spec["type"] == "bool" else
                 type(value) is int and spec["minimum"] <= value <= spec["maximum"] if spec["type"] == "int" else
                 value in [choice[1] for choice in spec["options"]])
        if not valid:
            raise ValueError("Invalid ovRTX setting: " + spec["label"])
    return result


def render_attributes(values):
    """RenderProduct attribute names and exact ovstage storage types."""
    values = validate_settings(values)
    pt = values["mode"] == "PathTracing"
    prefix = "omni:rtx:pt:" if pt else "omni:rtx:rtpt:"
    limits = prefix + ("limits:" if pt else "")
    result = {
        "omni:rtx:rendermode": (values["mode"], "token"),
        limits + "maxBounces": (values["max_bounces"], "uint32"),
        limits + "extraSpecularAndTransmissiveBounces": (values["extra_bounces"], "uint32"),
        prefix + "fireflyFilter:enabled": (values["firefly_filter"], "bool"),
    }
    if pt:
        result.update({prefix + "samplesPerPixel": (values["samples_per_pixel"], "uint32"),
                       prefix + "denoising:enabled": (values["denoising"], "bool")})
    return result


def refinement_frames(values):
    # A PathTracing step accumulates its complete sample budget internally.
    return 1 if values["mode"] == "PathTracing" else values["accumulation_frames"]
