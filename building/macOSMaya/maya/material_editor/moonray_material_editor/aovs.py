"""Serializable final-render AOV definitions, independent of Qt and USD."""
import re


TYPES = {"beauty": "Beauty", "alpha": "Alpha", "depth": "Depth",
         "material": "Material AOV", "lpe": "Light path expression",
         "extra": "Extra AOV label", "state": "State variable", "primvar": "Primitive attribute"}
STATES = {"P": 0, "Ng": 1, "N": 2, "St": 3, "dPds": 4, "dPdt": 5,
          "dSdx": 6, "dSdy": 7, "dTdx": 8, "dTdy": 9, "Wp": 10, "depth": 11, "motionvec": 12}
FILTERS = {"average": 0, "sum": 1, "min": 2, "max": 3, "force_consistent_sampling": 4, "closest": 5}
PRECISIONS = {"float": 0, "half": 1}
ATTRIBUTE_TYPES = {"FLOAT": 0, "VEC2F": 1, "VEC3F": 2, "RGB": 3}
DISPLAYS = {"auto": "Automatic", "srgb": "sRGB", "raw": "Raw / linear",
            "signed": "Signed vector", "normalize": "Normalize"}
# key: title, output type, source, display hint
PRESETS = {
    "beauty": ("Beauty", "beauty", "", "srgb"),
    "alpha": ("Alpha", "alpha", "", "raw"),
    "albedo": ("Albedo", "material", "albedo", "srgb"),
    "roughness": ("Roughness", "material", "roughness", "raw"),
    "normal": ("Shading normal", "state", "N", "signed"),
    "depth": ("Depth", "depth", "", "normalize"),
    "uv": ("UV coordinates", "state", "St", "raw"),
    "position": ("World position", "state", "Wp", "normalize"),
    "diffuse_direct": ("Direct diffuse", "lpe", "CDL", "srgb"),
    "glossy_direct": ("Direct glossy", "lpe", "CGL", "srgb"),
    "emission": ("Emission", "lpe", "CO", "srgb"),
    "custom_lpe": ("Custom LPE", "lpe", "", "srgb"),
    "custom_material": ("Custom material AOV", "material", "", "raw"),
    "custom_mask": ("Custom shader value / mask", "extra", "", "raw"),
    "custom_attribute": ("Custom primitive attribute", "primvar", "", "raw"),
}


def preset(key, enabled=True):
    _, kind, source, display = PRESETS[key]
    return dict(enabled=enabled, name=key, type=kind, source=source, precision="float",
                filter="average", display=display, attribute_type="FLOAT")


def default_outputs():
    return [preset(key, key in ("beauty", "alpha"))
            for key in ("beauty", "alpha", "albedo", "roughness", "normal", "depth")]


def validate_outputs(records, for_render=True):
    """Validate structure on load, and enabled definitions before a render.

    Incomplete drafts can be saved. MoonRay checks expression grammar during
    scene preparation; we do not approximate its LPE/material expression parser.
    """
    if not isinstance(records, list) or len(records) > 256:
        raise ValueError("AOV outputs must be a list of at most 256 entries.")
    result, names = [], set()
    for index, row in enumerate(records, 1):
        if not isinstance(row, dict) or type(row.get("enabled")) is not bool:
            raise ValueError(f"AOV {index}: invalid enabled state.")
        for field, allowed in (("type", TYPES), ("precision", PRECISIONS), ("filter", FILTERS),
                               ("display", DISPLAYS), ("attribute_type", ATTRIBUTE_TYPES)):
            if not isinstance(row.get(field), str) or row[field] not in allowed:
                raise ValueError(f"AOV {index}: invalid {field}.")
        for field in ("name", "source"):
            if not isinstance(row.get(field), str) or len(row[field]) > 2048 or any(ord(c) < 32 for c in row[field]):
                raise ValueError(f"AOV {index}: invalid {field}.")
        row = {key: row[key] for key in ("enabled", "name", "type", "source", "precision", "filter", "display", "attribute_type")}
        if for_render and row["enabled"]:
            name, kind, source = row["name"], row["type"], row["source"].strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in {"R", "G", "B", "A"}:
                raise ValueError(f"AOV {index}: use a channel name starting with a letter or underscore, followed by letters, digits or underscores; R/G/B/A are reserved.")
            if name in names:
                raise ValueError(f"AOV {index}: duplicate enabled channel name '{name}'.")
            names.add(name)
            if kind in ("material", "lpe", "extra", "state", "primvar") and not source:
                raise ValueError(f"AOV '{name}': enter a source or expression.")
            if kind == "state" and source not in STATES:
                raise ValueError(f"AOV '{name}': choose a state variable: {', '.join(STATES)}.")
            if kind == "extra" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source):
                raise ValueError(f"AOV '{name}': use a label with letters, digits or underscores, without the U: prefix.")
            row["source"] = source
        result.append(row)
    if for_render and not names:
        raise ValueError("Enable at least one AOV output in AOV Workflow.")
    return result


def output_properties(row):
    """Native RenderOutput attributes; destinations are owned by RenderView."""
    kind, source = row["type"], row["source"]
    props = dict(channel_name=row["name"], channel_format=PRECISIONS[row["precision"]],
                 math_filter=FILTERS[row["filter"]])
    if kind in ("beauty", "alpha", "depth"):
        props["result"] = {"beauty": 0, "alpha": 1, "depth": 2}[kind]
        if row["name"] == kind and kind in ("beauty", "alpha"):
            props["channel_name"] = ""
    elif kind == "material":
        props.update(result=7, material_aov=source)
    elif kind in ("lpe", "extra"):
        props.update(result=8, lpe=f"C'U:{source}'" if kind == "extra" else source)
    elif kind == "state":
        props.update(result=3, state_variable=STATES[source])
    elif kind == "primvar":
        props.update(result=4, primitive_attribute=source, primitive_attribute_type=ATTRIBUTE_TYPES[row["attribute_type"]])
    return props


def apply_display_hints(metadata, outputs):
    """Apply output display choices to discovered groups and single channels."""
    hints = {row["name"]: row["display"] for row in outputs if row["enabled"] and row["display"] != "auto"}
    for view in metadata["views"]:
        channels = [metadata["parts"][view["part"]]["channels"][i] for i in view["channels"]]
        keys = {"beauty" if c in ("R", "G", "B") else "alpha" if c == "A" else c.split(".")[0] for c in channels}
        if len(keys) == 1 and next(iter(keys)) in hints:
            view["display"] = hints[next(iter(keys))]
    return metadata
