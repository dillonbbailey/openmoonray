"""Persistent, one-way links from material preview cameras to their USD copies."""
import json

from pxr import Gf, UsdGeom

from .model import Graph, PROJECTION_CAMERAS
from .preview_camera import signature, snapshot

KEY = "moonrayEditor:previewCamera"


def is_locked(prim):
    data = prim.GetCustomDataByKey(KEY) if prim else None
    return bool(isinstance(data, dict) and data.get("locked"))


def record(stage):
    metadata = stage.GetRootLayer().customLayerData
    path = metadata.get("lunatic:previewCamera")
    prim = stage.GetPrimAtPath(path) if path else None
    if not prim or not prim.IsA(UsdGeom.Camera):
        return None
    data = prim.GetCustomDataByKey(KEY)
    if not data:
        # Existing preview copies already carry their source graph. Offer the
        # lock without changing the stage until the user enables it.
        try:
            graph = json.loads(metadata.get("lunatic:previewGraph", "{}"))
            data = dict(material_id=graph["material_id"], locked=False, signature="")
        except (ValueError, KeyError, TypeError):
            return None
    if not isinstance(data, dict) or not isinstance(data.get("material_id"), str):
        return None
    return dict(data, path=str(prim.GetPath()))


def associate(camera, graph, base_dir):
    data = snapshot(graph, base_dir)
    camera.GetPrim().SetCustomDataByKey(KEY, dict(material_id=data["material_id"], locked=True, signature=signature(data)))


def set_locked(stage, enabled):
    if type(enabled) is not bool:
        raise ValueError("Preview camera lock must be on or off.")
    link = record(stage)
    if not link:
        raise ValueError("Copy a material preview scene to USD Viewer first.")
    prim = stage.GetPrimAtPath(link.pop("path"))
    link.update(locked=enabled, signature="")
    prim.SetCustomDataByKey(KEY, link)


def write_camera(camera, kind, values, catalog, *, reset_transform=False):
    """Write the native square-preview camera in USD's camera units."""
    from .worker import usd_type, usd_value
    if kind not in PROJECTION_CAMERAS:
        raise ValueError("Unsupported preview camera: " + kind)
    prim = camera.GetPrim()
    def set_value(attr, value):
        # Re-locking replaces independent animated camera edits as well as
        # defaults. Author composed sample times in the current edit layer.
        samples = attr.GetTimeSamples()
        attr.Set(value)
        for time in samples:
            attr.Set(value, time)
    factor = 10. if kind == "OrthographicCamera" else 1.
    set_value(camera.CreateProjectionAttr(), "orthographic" if kind == "OrthographicCamera" else "perspective")
    set_value(camera.CreateFocalLengthAttr(), values.get("focal", 50.))
    set_value(camera.CreateHorizontalApertureAttr(), values["film_width_aperture"] * factor)
    set_value(camera.CreateVerticalApertureAttr(), values["film_width_aperture"] * factor)
    set_value(camera.CreateHorizontalApertureOffsetAttr(), values["horizontal_film_offset"] * factor)
    set_value(camera.CreateVerticalApertureOffsetAttr(), values["vertical_film_offset"] * factor)
    set_value(camera.CreateClippingRangeAttr(), Gf.Vec2f(values["near"], values["far"]))
    set_value(camera.CreateFStopAttr(), values["dof_aperture"] if values["dof"] else 0.)
    set_value(camera.CreateFocusDistanceAttr(), values["dof_focus_distance"])
    op_name = "xformOp:transform:materialPreview" if reset_transform else "xformOp:transform"
    op = UsdGeom.XformOp(prim.GetAttribute(op_name)) if prim.HasAttribute(op_name) else camera.AddTransformOp(
        UsdGeom.XformOp.PrecisionDouble, "materialPreview" if reset_transform else "")
    set_value(op.GetAttr(), Gf.Matrix4d(*[v for row in values["node_xform"] for v in row]))
    if reset_transform:
        camera.SetXformOpOrder([op], resetXformStack=True)
    mapped = {"node_xform", "focal", "film_width_aperture", "near", "far", "horizontal_film_offset",
              "vertical_film_offset", "dof", "dof_aperture", "dof_focus_distance"}
    for name, meta in catalog.attributes(kind).items():
        if name not in mapped and not meta["attrType"].startswith("SceneObject"):
            set_value(prim.CreateAttribute("moonray:" + name, usd_type(meta)), usd_value(meta, values[name]))
    # Older copies authored these native overrides; keep them consistent with
    # the USD attributes instead of leaving an old lens/DOF value in place.
    for name in mapped - {"node_xform"}:
        if name in values and prim.HasAttribute("moonray:" + name):
            set_value(prim.GetAttribute("moonray:" + name), usd_value(catalog.attributes(kind)[name], values[name]))


def apply_update(stage, data):
    from .usd_materials import material_catalog
    link = record(stage)
    if not link or not link.get("locked") or link["material_id"] != data.get("material_id"):
        raise ValueError("The preview camera link changed before it could synchronize.")
    # Validate the complete camera payload at the process boundary.
    catalog = material_catalog()
    graph = Graph(catalog)
    node = graph.add(data["shader"])
    node["values"] = data["values"]
    graph.validate()
    camera = UsdGeom.Camera(stage.GetPrimAtPath(link["path"]))
    write_camera(camera, data["shader"], data["values"], catalog, reset_transform=True)
    camera.GetPrim().SetCustomDataByKey(KEY, dict(material_id=link["material_id"], locked=True, signature=signature(data)))
