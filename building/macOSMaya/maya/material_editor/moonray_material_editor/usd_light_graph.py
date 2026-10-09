"""Read linked light graphs and apply their values in the USD edit target."""
import json
from functools import lru_cache
from pathlib import Path

from pxr import Sdf, Usd, UsdLux

from .light_graph import LIGHT_CLASSES, light_fields
from .model import Catalog, Graph, validate_value
from .usd_editing import editable_prim, encode_value
from .worker import usd_type, usd_value

USD_FIELDS = {"color": "inputs:color", "intensity": "inputs:intensity", "exposure": "inputs:exposure",
              "normalized": "inputs:normalize", "texture": "inputs:texture:file", "width": "inputs:width",
              "height": "inputs:height", "radius": "inputs:radius", "angular_extent": "inputs:angle"}
GRAPH_KEY = "moonrayEditor:lightGraph"


@lru_cache(maxsize=1)
def catalog():
    return Catalog()


def light_class(prim):
    if not editable_prim(prim) or not prim.HasAPI(UsdLux.LightAPI):
        return None
    kind = LIGHT_CLASSES.get(prim.GetTypeName())
    if not kind:
        return None
    override = prim.GetAttribute("moonray:class").Get()
    if override:
        kind = str(override)
    elif kind in {"DiskLight", "SphereLight"}:
        angle = prim.GetAttribute("inputs:shaping:cone:angle")
        if angle and angle.Get() is not None and angle.Get() < 90:
            kind = "SpotLight"
    return kind if kind in catalog().shaders and catalog().category(kind) == "Light" else None


def light_attribute(prim, name):
    native = prim.GetAttribute("moonray:" + name)
    if native and native.HasAuthoredValueOpinion():
        return native
    standard = prim.GetAttribute(USD_FIELDS.get(name, "")) if name in USD_FIELDS else None
    return standard if standard else native


def light_info(prim, frame=0):
    kind = light_class(prim)
    if not kind:
        raise ValueError("Select an editable, supported USD light.")
    values = {}
    attrs = catalog().attributes(kind)
    time = Usd.TimeCode(frame)
    for name in light_fields(kind):
        if name not in attrs:
            continue
        attr = light_attribute(prim, name)
        value = attr.Get(time) if attr else None
        if value is None and kind == "SpotLight":
            shaping = UsdLux.ShapingAPI(prim)
            outer = shaping.GetShapingConeAngleAttr().Get(time) if shaping else None
            if name == "lens_radius":
                value = prim.GetAttribute("inputs:radius").Get(time)
            elif outer is not None and name in {"outer_cone_angle", "inner_cone_angle"}:
                softness = shaping.GetShapingConeSoftnessAttr().Get(time) or 0
                value = 2 * outer * (max(0, 1 - softness) if name == "inner_cone_angle" else 1)
        if value is None and kind == "CylinderLight" and name == "height":
            value = prim.GetAttribute("inputs:length").Get(time)
        if value is None:
            value = attrs[name]["default"]
        if isinstance(value, str) and attrs[name].get("enum"):
            value = attrs[name]["enum"][value]
        if name == "texture" and value:
            if isinstance(value, Sdf.AssetPath):
                value = value.resolvedPath or value.path
            if not Path(value).is_absolute() and attr:
                stack = attr.GetPropertyStack(time)
                if stack:
                    value = Sdf.ComputeAssetPathRelativeToLayer(stack[0].layer, value)
        values[name] = encode_value(value)
    return dict(path=str(prim.GetPath()), shader=kind, name=prim.GetName(), values=values)


def linked_graph(prim, scene, frame=0):
    info = light_info(prim, frame)
    stored = prim.GetCustomDataByKey(GRAPH_KEY)
    graph = Graph(catalog(), json.loads(stored)) if stored else Graph(catalog())
    if not stored:
        node = graph.add(info["shader"])
        graph.data["light"] = node["id"]
        node["label"] = info["name"]
        graph.rename_material(info["name"] + " light")
    node = graph.node(graph.data["light"])
    if node["shader"] != info["shader"]:
        raise ValueError("The light type changed since its graph was applied.")
    values = dict(info["values"])
    if graph.incoming(node["id"], "color"):
        values.pop("texture", None)  # Retain the user's unconnected texture fallback.
    node["values"].update(values)
    node["ports"] = ["color"] if catalog().attributes(info["shader"])["color"].get("bindable") else []
    node["usd_light"] = dict(scene=scene, path=info["path"])
    geometry = prim.GetRelationship("inputs:geometry")
    if geometry:
        node["usd_geometry"] = ", ".join(map(str, geometry.GetForwardedTargets()))
    graph.validate()
    return graph.data


def apply_light_graph(stage, data, frame=0):
    path = data["reference"]["path"]
    prim = stage.GetPrimAtPath(path)
    if light_class(prim) != data["shader"]:
        raise ValueError("The referenced light was removed or its type changed. Reopen it from USD SCENE LIGHTS.")
    graph = Graph(catalog(), json.loads(data["graph"]))
    attrs = catalog().attributes(data["shader"])
    for name, value in data["values"].items():
        if name not in light_fields(data["shader"]) or name not in attrs:
            raise ValueError("Unsupported light input: " + name)
        validate_value(attrs[name], value)
        attr = light_attribute(prim, name)
        if attr and attr.HasAuthoredConnections():
            raise ValueError("Disconnect the existing USD connection on " + attr.GetName() + " before applying this graph.")
        if not attr:
            attr = prim.CreateAttribute("moonray:" + name, usd_type(attrs[name]), custom=True)
        if attr.GetTypeName() == Sdf.ValueTypeNames.Token and attrs[name].get("enum"):
            converted = next(key for key, number in attrs[name]["enum"].items() if number == value)
        else:
            converted = Sdf.AssetPath(value) if attr.GetTypeName() == Sdf.ValueTypeNames.Asset else usd_value(attrs[name], value)
        time = Usd.TimeCode(frame) if attr.GetNumTimeSamples() else Usd.TimeCode.Default()
        if attr.Get(time) == converted:
            continue
        # Authoring a sample in a stronger layer must retain the other keys.
        samples = [(t, attr.Get(t)) for t in attr.GetTimeSamples()] if not time.IsDefault() else []
        for t, sample in samples:
            attr.Set(sample, t)
        if not attr.Set(converted, time):
            raise ValueError("Could not update " + attr.GetPath().pathString)
    prim.SetCustomDataByKey(GRAPH_KEY, json.dumps(graph.data, separators=(",", ":")))
    return path
