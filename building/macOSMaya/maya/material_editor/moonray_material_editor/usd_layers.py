"""Layer inspection for the isolated USD worker."""
from pxr import Ar, Sdf

from .usd_composition import sublayer_entries


def retain_layers(stage, editing):
    from .usd_project import stage_layers
    for identifier, layer in stage_layers(stage).items():
        editing.layer_cache[identifier] = Sdf.Layer.FindOrOpen(identifier)
        if layer not in editing.initial:
            editing.initial[layer] = layer.ExportToString()


def layer_entries(stage, editing, sources=None):
    retain_layers(stage, editing)
    active_local = set(stage.GetLayerStack(includeSessionLayers=True))
    contributing = active_local | set(stage.GetUsedLayers())
    # Muted layers disappear from USD's layer stacks. Walk authored sublayer
    # links so they remain visible, including children of muted parents.
    local = []
    def visit(layer):
        if not layer or layer in local:
            return
        local.append(layer)
        for path in layer.subLayerPaths:
            visit(Sdf.Layer.FindRelativeToLayer(layer, path))
    with Ar.ResolverContextBinder(stage.GetPathResolverContext()):
        visit(stage.GetSessionLayer())
        visit(stage.GetRootLayer())
    external = sorted((layer for layer in stage.GetUsedLayers() if layer not in local),
                      key=lambda layer: layer.identifier)
    for identifier in stage.GetMutedLayers():
        layer = editing.layer_cache.get(identifier)
        if layer and layer not in local and layer not in external:
            external.append(layer)
    entries = []
    layers = [*local, *external]
    identifiers = {layer.identifier for layer in layers}
    with Ar.ResolverContextBinder(stage.GetPathResolverContext()):
        for layer in layers:
            role = ("Session" if layer == stage.GetSessionLayer() else
                    "Root" if layer == stage.GetRootLayer() else
                    "Sublayer" if layer in local else "Referenced")
            muted = stage.IsLayerMuted(layer.identifier)
            editable = layer in active_local and layer.permissionToEdit
            reason = ("Muted; this layer does not contribute to composition." if muted else
                      "Not contributing while an ancestor layer is muted." if layer in local and layer not in active_local else
                      "" if editable else "Layer does not permit editing." if layer in local else
                      "Referenced layers are inspectable; choose a local layer to author overrides.")
            sublayers = sublayer_entries(layer)
            children = []
            for sublayer in sublayers:
                # Resolve relative paths against their owning layer. Inspection
                # uses only already-open layers and never loads new scene data.
                child = Sdf.Layer.FindRelativeToLayer(layer, sublayer["path"])
                identifier = child.identifier if child and child.identifier in identifiers else None
                children.append(dict(sublayer, identifier=identifier))
            source = (sources or {}).get(layer.identifier) or layer.realPath
            name = (Sdf.Layer.GetDisplayNameFromIdentifier(source) if source else
                    ("anon:" if layer.anonymous else "") + layer.GetDisplayName())
            current = layer.ExportToString() if layer in editing.saved or layer in editing.initial else None
            entries.append(dict(identifier=layer.identifier, name=name, role=role,
                                anonymous=layer.anonymous, real_path=layer.realPath,
                                editable=editable, reason=reason, active=layer == editing.layer,
                                muted=muted, contributing=layer in contributing, can_mute=layer != stage.GetRootLayer(),
                                can_save=layer in local and layer.permissionToEdit and (layer.anonymous or layer.permissionToSave)
                                and (not source or source.lower().endswith((".usd", ".usda", ".usdc"))),
                                source=source,
                                modified=layer in editing.saved and current != editing.saved[layer],
                                can_revert=bool(layer.permissionToEdit and layer in editing.initial
                                                and current != editing.initial[layer]),
                                can_copy_edits=bool(layer in editing.initial and current != editing.initial[layer]),
                                sublayers=sublayers, children=children))
    return entries


def find_stage_layer(stage, identifier, editing=None):
    # Restrict requests to layers participating in this stage, including session.
    layer = next((layer for layer in [*stage.GetLayerStack(), *stage.GetUsedLayers()]
                  if layer.identifier == identifier), None)
    if layer is None and editing is not None:
        layer = editing.layer_cache.get(identifier)
    if layer is None:
        raise ValueError("This layer is no longer part of the open stage.")
    return layer


def layer_text(stage, identifier, editing=None):
    return find_stage_layer(stage, identifier, editing).ExportToString()


def layer_properties(stage, identifier, editing=None, sources=None):
    """Inspect only this layer's metadata, without flattening or loading assets."""
    from .usd_composition_inspection import layer_info
    from .usd_inspection import describe_value

    layer = find_stage_layer(stage, identifier, editing)
    info = layer_info(layer, sources or {})
    yes_no = lambda value: "Yes" if value else "No"
    rows = [
        ("Identifier", layer.identifier),
        ("Source file", info["source"] or "In memory"),
        ("File format", layer.GetFileFormat().formatId),
        ("Edit target", yes_no(layer == stage.GetEditTarget().GetLayer())),
        ("Muted", yes_no(stage.IsLayerMuted(identifier))),
        ("Contributing", yes_no(layer in stage.GetUsedLayers() or layer in stage.GetLayerStack())),
        ("Permission to edit", yes_no(layer.permissionToEdit)),
        ("Permission to save", yes_no(layer.permissionToSave)),
    ]
    if editing is not None:
        rows.append(("Unsaved edits", yes_no(editing.modified(layer))))
    # Sublayers are shown separately, with their own time mappings and strength.
    keys = sorted(set(layer.pseudoRoot.ListInfoKeys()) - {"subLayers", "subLayerOffsets"})
    metadata = [(key, describe_value(layer.pseudoRoot.GetInfo(key))) for key in keys]
    groups = [dict(name="Layer", rows=rows),
              dict(name="Authored metadata", rows=metadata or [("Metadata", "None authored")])]
    if layer.subLayerPaths:
        groups.append(dict(name="Sublayers · strongest first", rows=[
            (str(index + 1), f"{path}\nOffset: {offset.offset:g} · Scale: {offset.scale:g}")
            for index, (path, offset) in enumerate(zip(layer.subLayerPaths, layer.subLayerOffsets))]))
    return dict(info, groups=groups)
