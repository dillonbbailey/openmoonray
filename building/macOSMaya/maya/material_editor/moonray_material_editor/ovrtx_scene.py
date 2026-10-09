"""Private, composition-preserving scene handoff from the native USD viewer."""
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux

from .usd_project import anchor_asset, capture_stage, remap_assets


def matrix_values(matrix):
    return [[float(v) for v in row] for row in matrix]


def camera_packet(view):
    camera = Gf.Camera(view.resolveCamera()[0])
    # The free camera's focus defaults do not represent authored depth of field.
    if not view.getActiveSceneCamera():
        camera.fStop = 0
    return dict(transform=matrix_values(camera.transform),
                projection="orthographic" if camera.projection == Gf.Camera.Orthographic else "perspective",
                focalLength=float(camera.focalLength), horizontalAperture=float(camera.horizontalAperture),
                verticalAperture=float(camera.verticalAperture),
                horizontalApertureOffset=float(camera.horizontalApertureOffset),
                verticalApertureOffset=float(camera.verticalApertureOffset),
                clippingRange=[camera.clippingRange.min, camera.clippingRange.max],
                focusDistance=float(camera.focusDistance),
                fStop=float(camera.fStop))


def write_snapshot(stage, folder, retained=(), purposes=("proxy",), *, freeze=False, default_light=True):
    """Copy edited layers (all loaded layers when freezing); never flatten or save sources."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    from .usd_project import stage_layers
    inline_layers = tuple(stage_layers(stage, retained)) if freeze else (stage.GetRootLayer().identifier, stage.GetSessionLayer().identifier)
    data = capture_stage(stage, retained, inline=inline_layers)
    records = {r["identifier"]: r for r in data["layers"]}
    muted = set(data["muted_layers"])
    inline = {key for key, r in records.items() if "text" in r} | muted
    dependencies = {}
    for key, record in records.items():
        if "external" in record:
            source = Sdf.Layer.FindOrOpen(record["external"])
            dependencies[key] = {anchor_asset(source, path) for path in source.GetCompositionAssetDependencies()}
    while True:
        promoted = {key for key, paths in dependencies.items() if key not in inline and paths & inline}
        if not promoted:
            break
        inline.update(promoted)
    paths = {key: r["external"] for key, r in records.items() if key not in inline}
    paths.update({key: str(folder / f"layer-{index}.usdc") for index, key in enumerate(sorted(inline))})
    for key in inline:
        record = records[key]
        layer = Sdf.Layer.CreateAnonymous("ovrtx-copy.usda")
        if key not in muted:
            if "text" in record:
                if not layer.ImportFromString(record["text"]):
                    raise ValueError("Could not copy layer: " + record["name"])
            else:
                source = Sdf.Layer.FindOrOpen(record["external"])
                layer.TransferContent(Sdf.Layer.OpenAsAnonymous(record["external"]))
                remap_assets(layer, lambda path: anchor_asset(source, path))
            remap_assets(layer, lambda path: paths.get(path, path))
        if not layer.Export(paths[key]):
            raise ValueError("Could not write the ovRTX scene snapshot.")
    wrapper = Sdf.Layer.CreateAnonymous("ovrtx-scene.usda")
    wrapper.subLayerPaths = [paths[data["session"]], paths[data["root"]]]
    for key, value in stage.GetPseudoRoot().GetAllMetadata().items():
        if key not in ("subLayers", "subLayerOffsets"):
            wrapper.pseudoRoot.SetInfo(key, value)
    # Preserve payload load state and viewport purpose selection using private
    # opinions. None of these overrides are authored into the user's stage.
    copy = Usd.Stage.Open(wrapper, load=Usd.Stage.LoadNone)
    allowed = {"default", *purposes}
    for prim in stage.TraverseAll():
        if prim.HasPayload() and not prim.IsLoaded():
            copy.OverridePrim(prim.GetPath()).GetPayloads().SetPayloads([])
        if prim.IsA(UsdGeom.Gprim):
            purpose = str(UsdGeom.Imageable(prim).ComputePurpose())
            if purpose not in allowed:
                UsdGeom.Imageable(copy.OverridePrim(prim.GetPath())).CreateVisibilityAttr().Set("invisible")
    namespace = "/__MoonLabOvRTX"
    while stage.GetPrimAtPath(namespace):
        namespace += "_"
    # Match the viewer's fallback for scenes without authored lights. The black
    # dome used by Use default light also counts as authored and suppresses this.
    if default_light and not any(prim.HasAPI(UsdLux.LightAPI) for prim in stage.Traverse(Usd.TraverseInstanceProxies())):
        UsdGeom.Scope.Define(copy, namespace)
        UsdLux.DomeLight.Define(copy, namespace + "/DefaultLight").CreateIntensityAttr(1)
    destination = folder / "scene.usda"
    if not wrapper.Export(str(destination)):
        raise ValueError("Could not write the ovRTX scene wrapper.")
    return dict(scene=str(destination), namespace=namespace,
                time_codes_per_second=stage.GetTimeCodesPerSecond(),
                meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage), up_axis=str(UsdGeom.GetStageUpAxis(stage)))
