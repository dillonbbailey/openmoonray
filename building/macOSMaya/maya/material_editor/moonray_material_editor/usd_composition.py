"""Validated layer and prim composition operations for the native USD worker."""
import math
import os
from pathlib import Path
import tempfile

from pxr import Sdf, Usd


def sublayer_entries(layer):
    return [dict(path=path, offset=offset.offset, scale=offset.scale)
            for path, offset in zip(layer.subLayerPaths, layer.subLayerOffsets)]


def local_layer(stage, identifier):
    layer = next((layer for layer in stage.GetLayerStack() if layer.identifier == identifier), None)
    if layer is None or not layer.permissionToEdit:
        raise ValueError("Choose an editable layer in the local stage stack.")
    return layer


def validate_sublayers(layer, entries, expected):
    current = sublayer_entries(layer)
    if expected != current:
        raise ValueError("The sublayers changed. Reopen Edit sublayers to load the current list.")
    if not isinstance(entries, list):
        raise ValueError("Expected a list of sublayers.")
    old_paths = {entry["path"] for entry in current}
    seen = set()
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip() or path in seen:
            raise ValueError("Each sublayer must have a unique, nonempty asset path.")
        seen.add(path)
        if any(type(entry.get(key)) not in (int, float) or not math.isfinite(entry[key]) for key in ("offset", "scale")) or entry["scale"] == 0:
            raise ValueError("Sublayer offsets must be finite, with a nonzero time scale.")
        # Keep unresolved pre-existing paths editable/removable without requiring
        # the asset to become available before the list can be changed.
        if path in old_paths:
            continue
        child = Sdf.Layer.FindOrOpenRelativeToLayer(layer, path)
        if not child:
            raise ValueError("Could not open USD sublayer: " + path)
        pending, visited = [child], set()
        while pending:
            candidate = pending.pop()
            if candidate == layer:
                raise ValueError("This sublayer would create a layer cycle.")
            if candidate.identifier in visited:
                continue
            visited.add(candidate.identifier)
            for child_path in candidate.subLayerPaths:
                descendant = Sdf.Layer.FindOrOpenRelativeToLayer(candidate, child_path)
                if descendant:
                    pending.append(descendant)


def write_sublayers(layer, entries):
    with Sdf.ChangeBlock():
        layer.subLayerPaths = [entry["path"] for entry in entries]
        for index, entry in enumerate(entries):
            layer.subLayerOffsets[index] = Sdf.LayerOffset(entry["offset"], entry["scale"])


def new_sublayer(data):
    """Create an empty layer, returning its owning handle and any new file."""
    from .stage_metadata import validate_metadata
    from .usd_stage_metadata import author_layer_metadata
    metadata = validate_metadata(data.get("metadata", {}), defaults=True)
    storage = data.get("storage")
    if storage == "memory":
        name = data.get("name", "").strip()
        if not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\0", "\n")):
            raise ValueError("Enter a layer name, without a folder path.")
        if not Path(name).suffix:
            name += ".usda"
        if Path(name).suffix.lower() not in (".usd", ".usda", ".usdc"):
            raise ValueError("Use a .usd, .usda, or .usdc layer name.")
        layer = Sdf.Layer.CreateAnonymous(name)
        author_layer_metadata(layer, metadata)
        return layer, None
    if storage != "disk":
        raise ValueError("Choose In memory or On disk.")
    path = data.get("path", "").strip()
    if not path:
        raise ValueError("Choose a filename for the new sublayer.")
    target = Path(path).expanduser().absolute()
    if target.suffix.lower() not in (".usd", ".usda", ".usdc"):
        raise ValueError("Create the sublayer as .usd, .usda, or .usdc.")
    if target.exists() or target.is_symlink() or Sdf.Layer.Find(str(target)):
        raise ValueError("This layer already exists. Choose a new filename, or add it through Edit sublayers.")
    # Publish a complete empty file exclusively; even a file created after the
    # dialog's validation must never be replaced by this operation.
    fd, temporary = tempfile.mkstemp(prefix=".usd-sublayer-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    created = False
    try:
        empty = Sdf.Layer.CreateAnonymous("new-sublayer.usda")
        author_layer_metadata(empty, metadata)
        if not empty.Export(temporary):
            raise ValueError("Could not write the new sublayer.")
        os.link(temporary, target)
        created = True
        layer = Sdf.Layer.FindOrOpen(str(target))
        if not layer:
            raise ValueError("Could not open the new sublayer.")
        return layer, target
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_arc(stage, data):
    kind = data.get("kind")
    if kind not in ("reference", "payload"):
        raise ValueError("Choose a reference or payload.")
    prim = stage.GetPrimAtPath(data["path"])
    if not prim or prim.IsPseudoRoot() or not prim.IsActive() or prim.IsInstanceProxy() or prim.IsInPrototype():
        raise ValueError("Select an editable prim to add a reference or payload.")
    asset = data.get("asset", "").strip()
    if not asset:
        raise ValueError("Choose a USD asset file.")
    if asset.startswith("~"):
        asset = str(Path(asset).expanduser())
    layer = Sdf.Layer.FindOrOpenRelativeToLayer(stage.GetEditTarget().GetLayer(), asset)
    if not layer:
        raise ValueError("Could not open USD asset: " + asset)
    path_text = data.get("prim_path", "").strip()
    path = Sdf.Path(path_text) if path_text else Sdf.Path.emptyPath
    if path_text and (not path.IsAbsolutePath() or not path.IsPrimPath() or path.ContainsPrimVariantSelection()):
        raise ValueError("Use an absolute prim path, such as /Asset, or leave it empty for the default prim.")
    source = Usd.Stage.Open(layer, load=Usd.Stage.LoadNone)
    selected = source.GetPrimAtPath(path) if path_text else source.GetDefaultPrim()
    if not selected:
        raise ValueError("The asset has no prim at that path." if path_text else
                         "The asset has no default prim. Enter the source prim path, such as /Asset.")
    if layer in stage.GetLayerStack() and str(selected.GetPath()) == str(prim.GetPath()):
        raise ValueError("A prim cannot reference or payload itself.")
    return prim, asset, path


def write_arc(prim, kind, asset, path):
    api = prim.GetReferences() if kind == "reference" else prim.GetPayloads()
    success = api.AddReference(asset, path) if kind == "reference" else api.AddPayload(asset, path)
    if not success:
        raise ValueError("USD could not author this " + kind + ".")
    return str(prim.GetPath())


def set_payload_load(stage, path, loaded):
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsActive() or prim.IsInstanceProxy() or prim.IsInPrototype():
        raise ValueError("Select a prim or the stage root to load or unload its payloads.")
    if loaded:
        stage.Load(prim.GetPath(), Usd.LoadWithDescendants)
    else:
        stage.Unload(prim.GetPath())
    return str(prim.GetPath())
