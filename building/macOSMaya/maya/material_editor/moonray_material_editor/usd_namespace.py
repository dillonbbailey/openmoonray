"""Composed USD parenting through NamespaceEditor, preserving composition arcs."""
import json

from pxr import Sdf, Usd


def moved_load_rules(stage, source, destination):
    source, destination = Sdf.Path(source), Sdf.Path(destination)
    original = stage.GetLoadRules()
    rules = Usd.StageLoadRules()
    rules.SetRules([(path.ReplacePrefix(source, destination), rule)
                    for path, rule in original.GetRules()])
    # Retain inherited loading when a payload moves out of an unloaded parent.
    rules.AddRule(destination, original.GetEffectiveRuleForPath(source))
    rules.Minimize()
    return rules


def move_paths(stage, path, parent, name=None):
    from .usd_editing import editable_prim
    from .usd_transform_lock import require_unlocked
    source, destination_parent = Sdf.Path(path), Sdf.Path(parent)
    if not source.IsAbsolutePath() or not source.IsPrimPath() or source == Sdf.Path.absoluteRootPath:
        raise ValueError("Drag a prim, not the stage root.")
    if not destination_parent.IsAbsolutePath() or not destination_parent.IsAbsoluteRootOrPrimPath():
        raise ValueError("Choose a parent prim or the stage root (/).")
    prim, target = stage.GetPrimAtPath(source), stage.GetPrimAtPath(destination_parent)
    if not editable_prim(prim):
        raise ValueError("This prim cannot be moved. Unlock a linked preview camera first; instance contents must be edited at their source.")
    if not target or (not target.IsPseudoRoot() and not editable_prim(target, children=True)):
        raise ValueError("The destination cannot contain editable children.")
    if destination_parent.HasPrefix(source):
        raise ValueError("A prim cannot be parented beneath itself or its descendants.")
    for child in Usd.PrimRange(prim):
        require_unlocked(child)
    if name is not None and (not isinstance(name, str) or not Sdf.Path.IsValidIdentifier(name)):
        raise ValueError("Enter a valid USD prim name, without spaces or path separators.")
    destination = destination_parent.AppendChild(source.name if name is None else name)
    if destination == source:
        raise ValueError("The prim already has this name." if name is not None else "The prim already has this parent.")
    if stage.GetPrimAtPath(destination):
        raise ValueError("A prim already exists at " + str(destination) + ". Rename it or choose another parent.")
    if not stage.GetEditTarget().GetLayer().permissionToEdit:
        raise ValueError("The edit target does not permit editing.")
    return source, destination


def editor_for(stage, source, destination, mode):
    if mode not in ("namespace", "relocates"):
        raise ValueError("Choose Namespace edit or Relocates.")
    options = Usd.NamespaceEditor.EditOptions()
    options.allowRelocatesAuthoring = mode == "relocates"
    editor = Usd.NamespaceEditor(stage, options)
    if not editor.MovePrimAtPath(source, destination):
        raise ValueError("USD could not prepare this move.")
    return editor


def move_options(stage, path, parent):
    source, destination = move_paths(stage, path, parent)
    options = {}
    for mode in ("namespace", "relocates"):
        result = editor_for(stage, source, destination, mode).CanApplyEdits()
        options[mode] = dict(enabled=bool(result), reason=result.whyNot)
    return dict(path=str(source), parent=parent, destination=str(destination), options=options)


def remap_links(stage, source, destination):
    """Keep Lunatic's stored camera/material paths aligned with USD's path edits."""
    from .usd_material_sync import KEY
    from .usd_shader_graph import fingerprint
    def remap(value):
        path = Sdf.Path(value)
        return str(path.ReplacePrefix(source, destination)) if path.HasPrefix(source) else value
    root = stage.GetRootLayer()
    metadata = root.customLayerData
    camera = metadata.get("lunatic:previewCamera")
    if camera and remap(camera) != camera:
        metadata["lunatic:previewCamera"] = remap(camera)
        root.customLayerData = metadata
    for prim in stage.Traverse():
        stored = prim.GetCustomDataByKey(KEY)
        if not stored:
            continue
        record = json.loads(stored)
        link = record["link"]
        updated = dict(link, path=remap(link["path"]),
                       paths={key: remap(path) for key, path in link["paths"].items()},
                       interfaces=[remap(path) for path in link.get("interfaces", [])])
        if updated != link:
            updated["fingerprint"] = fingerprint(stage, updated)
            record["link"] = updated
            prim.SetCustomDataByKey(KEY, json.dumps(record, separators=(",", ":")))


def move_prim(stage, path, parent, mode, name=None):
    source, destination = move_paths(stage, path, parent, name)
    editor = editor_for(stage, source, destination, mode)
    result = editor.CanApplyEdits()
    if not result:
        raise ValueError(result.whyNot)
    if not editor.ApplyEdits():
        raise ValueError("USD could not apply this move.")
    remap_links(stage, source, destination)
    return str(destination)
