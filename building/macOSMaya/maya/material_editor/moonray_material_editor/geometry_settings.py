"""Shared tessellation controls and optional USD render overrides."""
import math
GEOMETRY_PREFIX = "lunatic:geometry:"
# Mesh resolution crosses a float32 USD/RDL attribute before conversion to a
# signed 32-bit int. INT_MAX rounds UP to 2**31 as float32; use its predecessor.
# This is also even, avoiding overflow when subdivision rounds odd values up.
TESSELLATION_MAX = 2**31 - 128
SHADOW_TERMINATOR_OPTIONS = [("Off", 0), ("On · recommended first", 1), ("Sine compensation", 2),
                             ("GGX compensation", 3), ("Cosine compensation", 4)]
TESSELLATION_HELP = (f"Maximum segments per original mesh edge (numeric range 1–{TESSELLATION_MAX:,}). "
                     "Subdivision meshes round odd values above 1 up to the next even value. "
                     "This caps adaptive tessellation; it is not a subdivision iteration count.")
ADAPTIVE_HELP = ("Target tessellated edge length in screen pixels, limited by the per-edge tessellation maximum. "
                 "Lower positive values request finer geometry; 0 uses uniform tessellation at the maximum. "
                 "This is separate from adaptive pixel sampling. MoonRay does not support adaptive tessellation for instances.")
GEOMETRY_DEFAULTS = {"override": False, "scheme": "authored", "mesh_resolution": 4, "adaptive_error": 1.0}
LAYER_MARKER = "lunaticRenderGeometry"


def geometry_specs():
    group = "Displacement and subdivision"
    return [dict(group=group, key=GEOMETRY_PREFIX + name, default=GEOMETRY_DEFAULTS[name], **spec) for name, spec in (
        ("override", dict(label="Override mesh tessellation", type="bool", minimum=None, maximum=None,
                          help="Override loaded mesh tessellation for this render. Off preserves authored mesh settings.")),
        ("scheme", dict(label="Subdivision scheme", type="string",
                        options=[("Keep authored scheme", "authored"), ("Catmull–Clark", "catmullClark"),
                                 ("Bilinear", "bilinear"), ("Polygon", "none")],
                        help="Keep the scene's per-mesh subdivision schemes, or explicitly override them.")),
        ("mesh_resolution", dict(label="Tessellation limit per edge", type="int", minimum=1,
                                 maximum=TESSELLATION_MAX, help=TESSELLATION_HELP)),
        ("adaptive_error", dict(label="Adaptive error · pixels", type="float", minimum=0.0, maximum=64.0,
                                help=ADAPTIVE_HELP)),
    )]


def geometry_values(settings):
    return {key: settings.get(GEOMETRY_PREFIX + key, default) for key, default in GEOMETRY_DEFAULTS.items()}


def find_geometry_layer(stage):
    from pxr import Sdf
    for identifier in stage.GetSessionLayer().subLayerPaths:
        layer = Sdf.Layer.Find(identifier)
        if layer and layer.customLayerData.get(LAYER_MARKER):
            return layer
    return None


def update_geometry_layer(stage, layer, values):
    """Only our dedicated layer is changed; authored mesh opinions remain intact."""
    from pxr import Sdf, UsdGeom
    before = layer.ExportToString()
    meshes = [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh) and not prim.IsInstanceProxy()]
    try:
        with Sdf.ChangeBlock():
            layer.Clear()
            layer.customLayerData = {LAYER_MARKER: True, **values}
            if values["override"]:
                for prim in meshes:
                    spec = Sdf.CreatePrimInLayer(layer, prim.GetPath())
                    for name in ("mesh_resolution", "adaptive_error"):
                        attr = Sdf.AttributeSpec(spec, "primvars:moonray:" + name, Sdf.ValueTypeNames.Float)
                        attr.default = float(values[name])
                        attr.SetInfo("interpolation", "constant")
                    if values["scheme"] != "authored":
                        Sdf.AttributeSpec(spec, "subdivisionScheme", Sdf.ValueTypeNames.Token,
                                          Sdf.VariabilityUniform).default = values["scheme"]
        # Strong opinions authored directly in the session layer must not silently
        # prevent these controls from taking effect.
        if values["override"]:
            for prim in meshes:
                if prim:
                    for name in ("mesh_resolution", "adaptive_error"):
                        attr = prim.GetAttribute("primvars:moonray:" + name)
                        if not math.isclose(attr.Get(), float(values[name]), rel_tol=1e-6, abs_tol=1e-7):
                            raise ValueError(f"A stronger session opinion on {prim.GetPath()} overrides {name}. Edit that mesh's property directly.")
                    if values["scheme"] != "authored" and prim.GetAttribute("subdivisionScheme").Get() != values["scheme"]:
                        raise ValueError(f"A stronger session opinion on {prim.GetPath()} overrides subdivisionScheme.")
    except Exception:
        layer.ImportFromString(before)
        raise


def create_geometry_layer(stage):
    from pxr import Sdf
    if not stage.GetSessionLayer().permissionToEdit:
        raise ValueError("The session layer does not permit render tessellation overrides.")
    layer = find_geometry_layer(stage)
    if layer is None:
        layer = Sdf.Layer.CreateAnonymous("render-tessellation.usda")
        layer.customLayerData = {LAYER_MARKER: True, **GEOMETRY_DEFAULTS}
        stage.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
    return layer
