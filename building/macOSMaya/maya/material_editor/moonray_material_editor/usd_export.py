"""Composition-preserving USD saves and explicitly requested flattened exports."""
import os
from pathlib import Path
import shutil
import tempfile

from pxr import Ar, Sdf, Usd

from .usd_project import anchor_asset, capture_stage, remap_assets


def save_composition(stage, destination, retained=()):
    """Write authored layers without baking references, payloads or variants.

    Keep unchanged dependencies external. Store anonymous/edited dependencies
    beside the entry layer, including clean ancestors whose arcs need remapping.
    Publish the entry file last so a failed save leaves the previous save usable.
    """
    destination = Path(destination)
    root, session = stage.GetRootLayer(), stage.GetSessionLayer()
    data = capture_stage(stage, retained, inline=(root.identifier, session.identifier))
    records = {record["identifier"]: record for record in data["layers"]}
    copies = {}

    def copy_layer(identifier):
        if identifier in copies:
            return copies[identifier]
        record = records[identifier]
        layer = Sdf.Layer.CreateAnonymous("save.usda")
        if "text" in record:
            if not layer.ImportFromString(record["text"]):
                raise ValueError("Cannot save USD layer: " + record["name"])
        else:
            source = Sdf.Layer.FindOrOpen(record["external"])
            layer.TransferContent(Sdf.Layer.OpenAsAnonymous(record["external"]))
            remap_assets(layer, lambda path: anchor_asset(source, path))
        copies[identifier] = layer
        return layer

    inline = {key for key, record in records.items() if "text" in record}
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

    # A nonempty session is the strongest layer stack. Retain it as a wrapper
    # over the authored root, preserving both stacks and all sublayer offsets.
    has_session = not session.empty or bool(session.pseudoRoot.ListInfoKeys())
    entry = data["session"] if has_session else data["root"]
    if not has_session:
        inline.discard(data["session"])
    output = copy_layer(entry)
    generation = None
    temporary = None
    try:
        sidecars = inline - {entry}
        paths = {key: record["external"] for key, record in records.items() if key not in inline and "external" in record}
        paths[entry] = str(destination)
        if sidecars:
            base = destination.parent / (destination.stem + "_layers")
            base.mkdir(exist_ok=True)
            generation = Path(tempfile.mkdtemp(prefix="save-", dir=base))
            for index, key in enumerate(sorted(sidecars)):
                paths[key] = str(generation / f"{index:03d}_{Path(records[key]['name']).stem}.usdc")
        if has_session:
            for name, value in stage.GetPseudoRoot().GetAllMetadata().items():
                if name not in ("subLayers", "subLayerOffsets"):
                    output.pseudoRoot.SetInfo(name, value)
            output.subLayerPaths.append(data["root"])

        for key in inline:
            layer = copy_layer(key)
            current = Path(paths[key])
            def relative(path):
                target = paths.get(path, path)
                # Resolver identifiers and packaged asset paths stay intact.
                if Path(target).is_absolute() and "[" not in target:
                    return os.path.relpath(target, current.parent)
                return target
            remap_assets(layer, relative)
            if key != entry and not layer.Export(str(current)):
                raise ValueError("USD could not save layer: " + str(current))
        fd, temporary = tempfile.mkstemp(prefix=".usd-save-", suffix=destination.suffix, dir=destination.parent)
        os.close(fd)
        if not output.Export(temporary):
            raise ValueError("USD could not save the stage.")
        os.replace(temporary, destination)
    except Exception:
        if generation:
            shutil.rmtree(generation)
        raise
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def flatten_for_save(stage):
    if not any(not prim.IsActive() for prim in stage.TraverseAll()):
        return stage.Flatten()
    # Flatten normally prunes inactive prims. Compose their contents in a
    # separate stage with private session overrides, then restore active=false
    # on the result. The live stage, its notices and undo history are untouched.
    with Ar.ResolverContextBinder(stage.GetPathResolverContext()):
        session = Sdf.Layer.CreateAnonymous("export-session.usda")
        session.TransferContent(stage.GetSessionLayer())
        anchor = stage.GetSessionLayer() if stage.GetSessionLayer().realPath else stage.GetRootLayer()
        remap_assets(session, lambda path: anchor_asset(anchor, path))
        clone = Usd.Stage.Open(stage.GetRootLayer(), session, stage.GetPathResolverContext(), load=Usd.Stage.LoadNone)
        clone.MuteAndUnmuteLayers(stage.GetMutedLayers(), [])
        clone.SetLoadRules(stage.GetLoadRules())
        clone.SetEditTarget(session)
        inactive = set()
        while True:
            paths = [prim.GetPath() for prim in clone.TraverseAll() if not prim.IsActive()]
            if not paths:
                break
            for path in paths:
                if path in inactive or not clone.GetPrimAtPath(path).SetActive(True):
                    raise ValueError("USD could not preserve the inactive prim: " + str(path))
                inactive.add(path)
        flattened = clone.Flatten()
        for path in inactive:
            spec = flattened.GetPrimAtPath(path)
            if spec is not None:
                spec.active = False
        return flattened
