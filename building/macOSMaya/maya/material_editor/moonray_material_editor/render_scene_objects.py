"""Discover and assign typed render objects in the native MoonRay runtime."""
from .render_view_config import OBJECT_SETTINGS, validate_render_request


def read_scene(path):
    import scene_rdl2
    context = scene_rdl2.SceneContext()
    context.setProxyModeEnabled(True)
    scene_rdl2.AsciiReader(context).fromFile(str(path))
    return context


def compatible_objects(context, key):
    interface = OBJECT_SETTINGS[key][1]
    return {name for name, types in context.getSceneObjectNamesAndTypes().items()
            if interface in types.split(" | ")}


def inspect_usd_cameras(path):
    from pxr import Usd, UsdGeom
    from .usd_schemas import register_schemas
    register_schemas()
    stage = Usd.Stage.Open(path)
    if not stage:
        raise ValueError("Could not open the USD scene.")
    return [dict(name=str(prim.GetPath()), type="USD Camera")
            for prim in stage.Traverse() if prim.IsA(UsdGeom.Camera)]


def inspect_scene_objects(data, directory):
    from .render_view_worker import prepare_scene
    key = data["object_setting"]
    if key not in OBJECT_SETTINGS:
        raise ValueError("Unknown scene object selector.")
    # A picker remains usable while unrelated AOVs or settings are being edited.
    data = dict(data)
    data.pop("outputs", None)
    data = validate_render_request(dict(data, camera="", settings={}, scene_objects={},
                                        passes=["beauty"], frame_mode="current"))
    if data["source"] != "material" and key in ("camera", "dicing_camera"):
        entries = inspect_usd_cameras(data["input"])
    else:
        context = read_scene(prepare_scene(data, directory))
        entries = [dict(name=name, type=context.getSceneObject(name).getSceneClass().getName())
                   for name in compatible_objects(context, key)]
    return dict(objects=sorted(entries, key=lambda item: item["name"].casefold()))


def import_dicing_camera(context, data, directory, name):
    """Hydra only syncs its active camera; translate a second camera when needed."""
    from .render_view_worker import prepare_scene
    other_dir = directory / "dicing-camera"
    other_dir.mkdir()
    other = read_scene(prepare_scene(dict(data, camera=name), other_dir))
    if name not in compatible_objects(other, "dicing_camera"):
        raise ValueError("The selected dicing camera was not translated: " + name)
    source = other.getSceneObject(name)
    target = context.createSceneObject(source.getSceneClass().getName(), name)
    for attr in source.getSceneClass().getAttributeNames():
        definition = source.getSceneClass().getAttribute(attr)
        for timestep in range(2 if definition.isBlurrable() else 1):
            value = source.get(attr, timestep)
            if hasattr(value, "toList"):
                value = value.toList()
                if definition.getTypeName().startswith("Mat"):
                    value = [component for row in value for component in row]
            if definition.getTypeName() == "SceneObject" and value is not None:
                if not context.sceneObjectExists(value.getName()):
                    raise ValueError("The dicing camera refers to an unavailable object: " + value.getName())
                value = context.getSceneObject(value.getName())
            if definition.isBlurrable():
                target.set(attr, value, timestep)
            else:
                target.set(attr, value)


def apply_scene_objects(data, scene, directory):
    selections = data.get("scene_objects", {})
    if not selections:
        return {}
    import scene_rdl2
    from .native import quote
    context = read_scene(scene)
    references = {}
    for key, name in selections.items():
        if key == "dicing_camera" and data["source"] != "material" and name not in compatible_objects(context, key):
            import_dicing_camera(context, data, directory, name)
        if name not in compatible_objects(context, key):
            raise ValueError(f"Selected {OBJECT_SETTINGS[key][0].lower()} is missing or has an incompatible type: {name}. Choose it again.")
        context.getSceneVariables().set(key, context.getSceneObject(name))
        references[key] = context.getSceneObject(name).getSceneClass().getName() + "(" + quote(name) + ")"
    scene_rdl2.writeSceneToFile(context, str(scene))
    return references
