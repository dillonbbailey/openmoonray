"""Layer-preserving project snapshots. Imported only in the native USD worker."""
import hashlib
from pathlib import Path

from pxr import Ar, Sdf, Usd, UsdUtils


RULES = {"all": Usd.StageLoadRules.AllRule, "none": Usd.StageLoadRules.NoneRule,
         "only": Usd.StageLoadRules.OnlyRule}


def remap_assets(layer, callback):
    # ModifyAssetPaths replaces subLayerPaths, which resets their offsets.
    offsets = list(layer.subLayerOffsets)
    UsdUtils.ModifyAssetPaths(layer, callback)
    for index, offset in enumerate(offsets):
        layer.subLayerOffsets[index] = offset


def anchor_asset(layer, path):
    if not path or path.startswith("anon:"):
        return path
    anchored = Sdf.ComputeAssetPathRelativeToLayer(layer, path)
    # Bare MDL modules use the renderer's material library search path. If USD
    # cannot resolve one locally, keep that lookup instead of inventing a file
    # beside the layer (e.g. ConceptCar/OmniPBR.mdl).
    if anchored == path and path.endswith(".mdl") and Path(path).name == path:
        return path
    # Search-relative paths that do not exist yet (including UDIM patterns)
    # otherwise remain relative and lose their original directory on reopen.
    if anchored == path and layer.realPath and not Path(path).is_absolute() and ":" not in path:
        anchored = Ar.GetResolver().CreateIdentifier("./" + path, layer.resolvedPath)
    return anchored


class ProjectLayers(list):
    """Own restored layers and remember their file origins across project saves."""
    def __init__(self, layers=()):
        super().__init__(layers)
        self.identifiers = {}
        self.sources = {}
        self.baselines = {}


def stage_layers(stage, retained=()):
    layers = {layer.identifier: layer for layer in (*retained, *stage.GetUsedLayers(), *stage.GetLayerStack())}
    for identifier in stage.GetMutedLayers():
        layer = Sdf.Layer.Find(identifier)
        if layer:
            layers[identifier] = layer
    pending = list(layers.values())
    while pending:
        layer = pending.pop()
        # Anonymous dependencies in inactive prims/unselected variants are not
        # necessarily reported by GetUsedLayers, but cannot be left dangling.
        for path in layer.GetCompositionAssetDependencies():
            if path.startswith("anon:") and path not in layers:
                dependency = Sdf.Layer.Find(path)
                if dependency:
                    layers[path] = dependency
                    pending.append(dependency)
    return layers


def layer_text(layer, anchor, identifiers):
    clone = Sdf.Layer.CreateAnonymous("snapshot.usda")
    clone.TransferContent(layer)
    def resolve(path):
        anchored = anchor_asset(anchor, path)
        return identifiers.get(path, identifiers.get(anchored, anchored))
    remap_assets(clone, resolve)
    return clone.ExportToString()


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def disk_baseline(source, identifiers):
    """Read disk independently of a possibly edited layer in USD's live cache."""
    try:
        anchor = Sdf.Layer.FindOrOpen(source)
        fresh = Sdf.Layer.OpenAsAnonymous(source)
        return fingerprint(layer_text(fresh, anchor, identifiers)) if fresh and anchor else None
    except Exception:
        return None


def capture_stage(stage, retained=(), *, inline=()):
    """Serialize authored layers and their composition arcs, never a flattened stage.

    Keep each layer separate, including inactive variant opinions and reference,
    payload, inherit, specialize, and sublayer arcs. Stage.Export(), Flatten(),
    and FlattenLayerStack() would erase part of that editable composition.
    """
    # Also retain unloaded project payloads: their anonymous layers are not in
    # GetUsedLayers(), but must survive another save/load cycle.
    layers = stage_layers(stage, retained)
    identifiers = {key: getattr(retained, "identifiers", {}).get(key, key) for key in layers}
    # A live file and a restored copy may both be used after adding a reference.
    # The actual file keeps its identifier; the isolated copy keeps its own
    # anonymous identity so their potentially different opinions cannot collide.
    for key, identifier in identifiers.items():
        if key != identifier and identifier in layers:
            identifiers[key] = key
    seen = set()
    for key, identifier in identifiers.items():
        if identifier in seen:
            identifiers[key] = key
        seen.add(identifiers[key])
    records = []
    with Ar.ResolverContextBinder(stage.GetPathResolverContext()):
        for identifier, layer in sorted(layers.items()):
            record = dict(identifier=identifiers[identifier], name=layer.GetDisplayName())
            source = getattr(retained, "sources", {}).get(identifier) or (layer.identifier if not layer.anonymous else None)
            available = bool(source and Ar.GetResolver().Resolve(Sdf.Layer.SplitIdentifier(source)[0]))
            if available and not layer.anonymous and not layer.dirty and identifier not in inline:
                record["external"] = source
                records.append(record)
                continue
            anchor = layer if layer.realPath else stage.GetRootLayer()
            text = layer_text(layer, anchor, identifiers)
            baseline = getattr(retained, "baselines", {}).get(identifier)
            if source and baseline is None:
                baseline = disk_baseline(source, identifiers)
            if available and identifier not in inline and baseline == fingerprint(text):
                record["external"] = source
            else:
                record["text"] = text
                if source:
                    record["source"] = source
            records.append(record)
    return dict(version=2, layers=records, root=identifiers[stage.GetRootLayer().identifier],
                session=identifiers[stage.GetSessionLayer().identifier], target=identifiers[stage.GetEditTarget().GetLayer().identifier],
                muted_layers=[identifiers.get(identifier, identifier) for identifier in stage.GetMutedLayers()],
                load_rules=[[str(path), next(name for name, rule in RULES.items() if rule == value)]
                            for path, value in stage.GetLoadRules().GetRules()])


def restore_stage(data):
    """Build isolated copies, validating before touching the currently open stage."""
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list) or not data["layers"]:
        raise ValueError("Project has no USD layers.")
    copies = {}
    retained = ProjectLayers()
    for record in data["layers"]:
        if (not isinstance(record, dict) or not all(isinstance(record.get(key), str) for key in ("identifier", "name"))
                or ("text" in record) == ("external" in record)
                or not isinstance(record.get("text", record.get("external")), str)
                or "source" in record and not isinstance(record["source"], str)):
            raise ValueError("Invalid USD layer in project.")
        identifier = record["identifier"]
        if identifier in copies:
            raise ValueError("Duplicate USD layer in project.")
        name = Path(record["name"]).stem or "layer"
        layer = Sdf.Layer.CreateAnonymous(name + ".usda")
        external = record.get("external")
        if external:
            try:
                original = Sdf.Layer.FindOrOpen(external)
                fresh = Sdf.Layer.OpenAsAnonymous(external)
            except Exception as exc:
                raise ValueError("Cannot open project USD dependency: " + external) from exc
            if not original or not fresh:
                raise ValueError("Cannot open project USD dependency: " + external)
            layer.TransferContent(fresh)
            remap_assets(layer, lambda path: anchor_asset(original, path))
        elif not layer.ImportFromString(record["text"]):
            raise ValueError("Cannot read project layer: " + record["name"])
        copies[identifier] = layer
        retained.append(layer)
        retained.identifiers[layer.identifier] = identifier
        source = external or record.get("source") or (identifier if not identifier.startswith("anon:") else None)
        if source:
            retained.sources[layer.identifier] = source
    if any(data.get(key) not in copies for key in ("root", "session", "target")):
        raise ValueError("Project is missing its root, session or edit-target layer.")
    if data["root"] == data["session"]:
        raise ValueError("Project root and session layers must be different.")
    for layer in copies.values():
        remap_assets(layer, lambda path: copies[path].identifier if path in copies else path)
    for layer in retained:
        source = retained.sources.get(layer.identifier)
        if source:
            retained.baselines[layer.identifier] = disk_baseline(source, retained.identifiers)
    rules = Usd.StageLoadRules()
    entries = []
    for entry in data.get("load_rules", []):
        if not isinstance(entry, list) or len(entry) != 2 or entry[1] not in RULES:
            raise ValueError("Invalid payload load rules in project.")
        path = Sdf.Path(entry[0])
        if not path.IsAbsolutePath() or not path.IsAbsoluteRootOrPrimPath():
            raise ValueError("Invalid payload path in project.")
        entries.append((path, RULES[entry[1]]))
    rules.SetRules(entries)
    stage = Usd.Stage.Open(copies[data["root"]], copies[data["session"]], load=Usd.Stage.LoadNone)
    muted = data.get("muted_layers", [])
    if not isinstance(muted, list) or any(not isinstance(identifier, str) or identifier not in copies or identifier == data["root"] for identifier in muted):
        raise ValueError("Invalid muted layers in project.")
    stage.MuteAndUnmuteLayers([copies[identifier].identifier for identifier in muted], [])
    stage.SetLoadRules(rules)
    target = copies[data["target"]]
    if target not in stage.GetLayerStack():
        raise ValueError("Project edit target is outside the local layer stack.")
    stage.SetEditTarget(stage.GetEditTargetForLocalLayer(target))
    # The owning handles must outlive the stage (including unloaded payloads).
    return stage, retained, target
