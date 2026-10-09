"""Bounded, read-only USD property descriptions for the isolated worker."""
from itertools import islice
import math

from pxr import Sdf, Usd
from .usd_editing import editable_prim, supported_type
from .usd_transform_lock import is_locked, is_transform_property
from .number_format import compact_number


def describe_value(value, depth=0):
    """Avoid serializing entire meshes or large dictionaries into the UI pipe."""
    if value is None:
        return "No value"
    if depth >= 4:
        return "…"
    if isinstance(value, Sdf.AssetPath):
        text = value.path
        if value.resolvedPath:
            text += "\nResolved: " + value.resolvedPath
    elif isinstance(value, float):
        text = compact_number(value)
    elif type(value).__module__ == "pxr.Gf" and hasattr(value, "GetReal"):
        text = "(" + ", ".join(describe_value(v, depth + 1) for v in (value.GetReal(), *value.GetImaginary())) + ")"
    elif type(value).__module__ == "pxr.Gf" and hasattr(value, "dimension"):
        text = "(" + ", ".join(describe_value(v, depth + 1) for v in value) + ")"
    elif isinstance(value, dict):
        parts = [str(k) + ": " + describe_value(v, depth + 1) for k, v in islice(value.items(), 16)]
        if len(value) > 16:
            parts.append(f"… ({len(value)} entries total)")
        text = "{\n" + "\n".join(parts) + "\n}"
    elif isinstance(value, (list, tuple)) or type(value).__module__ == "pxr.Vt" and hasattr(value, "__len__"):
        parts = [describe_value(v, depth + 1) for v in islice(value, 16)]
        if len(value) > 16:
            parts.append(f"… ({len(value)} elements total)")
        text = "[" + ", ".join(parts) + "]"
    else:
        text = str(value)
    return text if len(text) <= 4096 else text[:4096] + "… (truncated)"


def property_rows(prim, time, animated_only=False):
    if prim.IsPseudoRoot():
        if animated_only:
            return []
        from .usd_stage_metadata import stage_metadata
        from .stage_metadata import FIELDS
        values = dict(prim.GetAllMetadata(), **stage_metadata(prim.GetStage()))
        return [dict(group="Metadata", name=name, type="token" if name == "upAxis" else type(value).__name__,
                     value=describe_value(value), editable=name in FIELDS and prim.GetStage().GetSessionLayer().permissionToEdit
                     and not prim.GetStage().IsLayerMuted(prim.GetStage().GetSessionLayer().identifier))
                for name, value in sorted(values.items())]
    rows = []
    sources = {
        Usd.ResolveInfoSourceNone: "No value",
        Usd.ResolveInfoSourceFallback: "Schema fallback",
        Usd.ResolveInfoSourceDefault: "Authored default",
        Usd.ResolveInfoSourceTimeSamples: "Time samples",
        Usd.ResolveInfoSourceValueClips: "Value clips",
    }
    for attr in sorted(prim.GetAttributes(), key=lambda a: a.GetName()):
        if animated_only and not attr.ValueMightBeTimeVarying():
            continue
        row = {"group": "Attributes", "name": attr.GetName(), "type": str(attr.GetTypeName()),
               "property_path": str(attr.GetPath()), "authored": attr.HasAuthoredValueOpinion(),
               "uniform": attr.GetVariability() == Sdf.VariabilityUniform,
               "editable": editable_prim(prim) and supported_type(attr.GetTypeName())
                           and not (is_locked(prim) and is_transform_property(attr.GetName()))}
        try:
            value = attr.Get(time)
            row.update(value=describe_value(value), samples=attr.GetNumTimeSamples(),
                       animated=attr.ValueMightBeTimeVarying(),
                       source=sources.get(attr.GetResolveInfo(time).GetSource(), "Resolved"),
                       connections=describe_value(attr.GetConnections()) if attr.HasAuthoredConnections() else "")
            if row["type"] in ("float", "double", "token"):
                inline = attr.GetTypeName().defaultValue if value is None else value
                if isinstance(inline, str) or isinstance(inline, (int, float)) and math.isfinite(inline):
                    row["inline_value"] = inline
                if row["type"] == "token":
                    row["options"] = list(attr.GetMetadata("allowedTokens") or [])
            else:
                default = attr.GetTypeName().defaultValue
                # Gf vector roles (color, point, normal, texCoord) use the same
                # component types. Arrays, matrices and quaternions stay bounded
                # text values in the tree and use the detailed property editor.
                if type(default).__module__ == "pxr.Gf" and getattr(default, "dimension", None) in (2, 3, 4):
                    components = list(default if value is None else value)
                    if all(math.isfinite(component) for component in components):
                        row["inline_value"] = components
                        suffix = type(default).__name__[-1]
                        row["component_type"] = {"d": "double", "i": "int", "h": "half"}.get(suffix, "float")
        except Exception as exc:
            row["value"] = "Unavailable: " + str(exc)
        rows.append(row)
    if not animated_only:
        for rel in sorted(prim.GetRelationships(), key=lambda r: r.GetName()):
            rows.append({"group": "Relationships", "name": rel.GetName(), "type": "relationship",
                         "property_path": str(rel.GetPath()),
                         "value": describe_value(rel.GetTargets()), "editable": editable_prim(prim)})
        for name, value in sorted(prim.GetAllMetadata().items()):
            rows.append({"group": "Metadata", "name": name, "type": type(value).__name__,
                         "value": describe_value(value), "editable": editable_prim(prim) and not prim.IsPseudoRoot()
                         and name in ("documentation", "displayName", "hidden", "kind")})
    return rows
