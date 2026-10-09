"""Read-only, bounded composition diagnostics for the native USD worker."""
from itertools import islice

from pxr import Pcp, Sdf, Usd

from .usd_inspection import describe_value
from .usd_composition_tree import SpecGroups, layer_tree_entries, node_parent

LIMIT = 500
ARC_NAMES = {Pcp.ArcTypeRoot: "Local", Pcp.ArcTypeReference: "Reference",
             Pcp.ArcTypePayload: "Payload", Pcp.ArcTypeInherit: "Inherit",
             Pcp.ArcTypeSpecialize: "Specialize", Pcp.ArcTypeVariant: "Variant",
             Pcp.ArcTypeRelocate: "Relocate"}
SOURCES = {Usd.ResolveInfoSourceNone: "No value", Usd.ResolveInfoSourceFallback: "Schema fallback",
           Usd.ResolveInfoSourceDefault: "Authored default", Usd.ResolveInfoSourceTimeSamples: "Time samples",
           Usd.ResolveInfoSourceValueClips: "Value clips"}


def layer_info(layer, sources):
    if not layer:
        return dict(name="—", identifier="", source="")
    source = sources.get(layer.identifier) or layer.realPath
    name = (Sdf.Layer.GetDisplayNameFromIdentifier(source) if source else
            ("anon:" if layer.anonymous else "") + layer.GetDisplayName())
    return dict(name=name, identifier=layer.identifier, source=source or "")


def layer_details(layer, sources):
    info = layer_info(layer, sources)
    return ["Layer: " + info["identifier"], "Source file: " + (info["source"] or "In memory")]


def offset_text(offset):
    return f"Offset {offset.offset:g}, scale {offset.scale:g}"


def authored_fields(spec):
    # Time samples can contain whole meshes; show their count and bracketing
    # values separately instead of requesting the full timeSamples dictionary.
    keys = sorted(key for key in spec.ListInfoKeys() if key != "timeSamples")
    lines = [f"{key}: {describe_value(spec.GetInfo(key))}" for key in keys[:40]]
    if len(keys) > 40:
        lines.append("… (authored fields truncated)")
    return lines


def inspect_composition(stage, path, mode, time, *, name="", group="Attributes", sources=None):
    sources = sources or {}
    prim = stage.GetPrimAtPath(path)
    if not prim:
        raise ValueError("This prim no longer exists.")
    result = dict(path=path, mode=mode, name=name, group=group, frame=time.GetValue(), rows=[],
                  summary="", resolved="", truncated=False)
    rows = result["rows"]
    # Pcp node handles borrow their expanded prim index from the query. Retain
    # the query and its arcs for the entire inspection, including layer walks.
    query = Usd.PrimCompositionQuery(prim) if mode in ("layers", "arcs") and not prim.IsPseudoRoot() else None
    arcs = query.GetCompositionArcs() if query else []
    nodes = [arc.GetTargetNode() for arc in arcs[:LIMIT]]
    groups = SpecGroups(result, layer_info, layer_details, sources, LIMIT)

    def row(cells, details, **extra):
        if len(rows) < LIMIT:
            detail = "\n".join(details)
            extra.setdefault("id", f"row:{len(rows)}")
            rows.append(dict(cells=cells, details=detail[:8192] + ("\n… (truncated)" if len(detail) > 8192 else ""), **extra))
        else:
            result["truncated"] = True

    if mode == "specs":
        result["headers"] = ["Hierarchy / strength", "Layer", "Specifier", "Spec path"]
        result["summary"] = "Specs grouped by their layer and containing prim specs. Numbers rank opinions strongest to weakest."
        specs = [] if prim.IsPseudoRoot() else prim.GetPrimStackWithLayerOffsets()
        for index, (spec, offset) in enumerate(islice(specs, LIMIT + 1)):
            specifier = {Sdf.SpecifierDef: "def", Sdf.SpecifierOver: "over", Sdf.SpecifierClass: "class"}[spec.specifier]
            row([str(index + 1), layer_info(spec.layer, sources)["name"], specifier, str(spec.path)],
                [*layer_details(spec.layer, sources), "Spec: " + str(spec.path), offset_text(offset), "", *authored_fields(spec),
                 "", "Authored properties:", *list(islice(spec.properties.keys(), 80)),
                 "… (truncated)" if len(spec.properties) > 80 else ""], parent=groups.parent(spec))
    elif mode == "layers":
        result["headers"] = ["Stack", "Strength", "Layer", "Time mapping"]
        result["summary"] = "Composition nodes contain their layer trees. Sublayers are children of their owning layer; numbers rank strength within each stack. Muted layers are excluded."
        # GetLayerStack alone omits referenced/payload layer stacks. Include
        # each composition node's stack, preserving its target path and offset.
        stacks = [("Stage", None)] if prim.IsPseudoRoot() else [
            (f"{ARC_NAMES.get(arc.GetArcType(), str(arc.GetArcType()))} {arc.GetTargetPrimPath()}", arc.GetTargetNode())
            for arc in arcs[:LIMIT]]
        for number, (label, node) in enumerate(stacks):
            node_id = f"node:{number}"
            result["groups"].append(dict(id=node_id, parent=node_parent(number, nodes) if node else None,
                                         cells=[label], details="Composition node: " + label, group=True))
            layers = stage.GetLayerStack() if node is None else node.layerStack.layers
            for layer, cumulative, occurrence, parent in islice(layer_tree_entries(stage, node), LIMIT + 1):
                index = layers.index(layer)
                row([label, str(index + 1), layer_info(layer, sources)["name"], offset_text(cumulative)],
                    [label, *layer_details(layer, sources), "Stage time = layer time × scale + offset",
                     offset_text(cumulative), "Edit target: " + ("Yes" if layer == stage.GetEditTarget().GetLayer() else "No")],
                    id=node_id + ":layer:" + occurrence,
                    parent=node_id + ":layer:" + parent if parent is not None else node_id,
                    label=layer_info(layer, sources)["name"])
            if result["truncated"]:
                break
    elif mode == "arcs":
        result["headers"] = ["Strength", "Arc", "Target layer", "Target path", "Has specs"]
        result["summary"] = "Composition arcs nested beneath their parent composition nodes. Numbers rank strength; includes arcs inherited from ancestors."
        for index, arc in enumerate(islice(arcs, LIMIT + 1)):
            target = arc.GetTargetLayer()
            origin = arc.GetIntroducingLayer()
            node = arc.GetTargetNode()
            row([str(index + 1), ARC_NAMES.get(arc.GetArcType(), str(arc.GetArcType())),
                 layer_info(target, sources)["name"], str(arc.GetTargetPrimPath()), "Yes" if arc.HasSpecs() else "No"],
                [*layer_details(target, sources), "Target prim: " + str(arc.GetTargetPrimPath()),
                 "Introduced in: " + (origin.identifier if origin else "Local layer stack"),
                 "Introduced at: " + str(arc.GetIntroducingPrimPath()),
                 "Ancestral: " + ("Yes" if arc.IsAncestral() else "No"),
                 "Implicit: " + ("Yes" if arc.IsImplicit() else "No"),
                 "Can contribute specs: " + ("Yes" if node.CanContributeSpecs() else "No"),
                 "Time mapping to stage: " + offset_text(node.mapToRoot.timeOffset)],
                id=f"node:{index}", parent=node_parent(index, nodes) if index < len(nodes) else None)
    elif mode == "values":
        result["headers"] = ["Hierarchy / strength", "Layer", "Opinion", "Authored value", "Spec path"]
        if group not in ("Attributes", "Relationships"):
            raise ValueError("Value resolution is available for attributes and relationships.")
        prop = prim.GetAttribute(name) if group == "Attributes" else prim.GetRelationship(name)
        if not prop:
            raise ValueError("Choose an attribute or relationship on this prim.")
        relation = group == "Relationships"
        info = None if relation else prop.GetResolveInfo(time)
        source = "Composed relationship targets" if relation else SOURCES.get(info.GetSource(), str(info.GetSource()))
        value = prop.GetTargets() if relation else prop.Get(time)
        blocked = not relation and info.ValueIsBlocked()
        result["summary"] = f"{name} · Frame {time.GetValue():g} · " + ("Blocked · " if blocked else "") + source
        result["resolved"] = "Resolved value:\n" + describe_value(value)
        if relation:
            result["resolved"] += "\n\nForwarded targets:\n" + describe_value(prop.GetForwardedTargets())
        elif prop.HasAuthoredConnections():
            result["resolved"] += "\n\nConnections (not evaluated by USD attribute resolution):\n" + describe_value(prop.GetConnections())
        winner = None
        for index, (spec, offset) in enumerate(islice(prop.GetPropertyStackWithLayerOffsets(time), LIMIT + 1)):
            count = 0 if relation else spec.layer.GetNumTimeSamplesForPath(spec.path)
            opinion = ("Targets" if spec.HasInfo("targetPaths") else "Metadata only") if relation else (
                "Time samples" if count else "Default" if spec.HasInfo("default") else "Metadata only")
            value = spec.GetInfo("targetPaths") if relation and spec.HasInfo("targetPaths") else spec.default if not relation else None
            details = [*layer_details(spec.layer, sources), "Spec: " + str(spec.path), offset_text(offset)]
            if count:
                layer_time = (time.GetValue() - offset.offset) / offset.scale
                found, lower, upper = spec.layer.GetBracketingTimeSamplesForPath(spec.path, layer_time)
                samples = [(t, spec.layer.QueryTimeSample(spec.path, t)) for t in dict.fromkeys((lower, upper))] if found else []
                value = "\n".join(f"{t:g}: {describe_value(v)}" for t, v in samples)
                details += [f"Time samples: {count}", f"Current layer time: {layer_time:g}"]
            else:
                value = describe_value(value)
            # Use the actual resolve source; a property stack can contain
            # metadata-only specs above the winning value or value clips.
            authored_value = count or spec.HasInfo("default")
            if not relation and winner is None and authored_value and (
                    blocked or info.GetSource() in (Usd.ResolveInfoSourceDefault, Usd.ResolveInfoSourceTimeSamples)):
                winner = index
                opinion += " · Blocked" if blocked else " · Resolved source"
            row([str(index + 1), layer_info(spec.layer, sources)["name"], opinion, value, str(spec.path)],
                [*details, "", *authored_fields(spec), "", "Authored value:\n" + value],
                winner=index == winner, parent=groups.parent(spec))
        if not relation and info.GetSource() == Usd.ResolveInfoSourceValueClips:
            result["resolved"] += "\n\nValue clips resolve at the current frame; the stack is diagnostic and no ordinary layer is marked as the winner."
        if not rows:
            result["resolved"] += "\n\nNo authored property specs."
    else:
        raise ValueError("Unknown composition inspection view.")
    if result["truncated"]:
        result["summary"] += f" Showing the first {LIMIT} rows."
    if not rows and mode != "values":
        result["summary"] += " No contributing entries."
    return result
