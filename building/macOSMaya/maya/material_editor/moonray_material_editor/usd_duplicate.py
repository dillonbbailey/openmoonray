"""Duplicate authored USD prims without flattening their composition."""
from pxr import Pcp, Sdf, Usd, UsdGeom


def duplicate_prim(stage, path, mode):
    from .usd_editing import editable_prim
    source = Sdf.Path(path)
    prim = stage.GetPrimAtPath(source)
    if mode not in ("copy", "instance"):
        raise ValueError("Choose New prim or Instance.")
    if not editable_prim(prim) or prim.IsPseudoRoot():
        raise ValueError("Select an editable prim. Instance contents must be duplicated at their source.")
    parent = prim.GetParent()
    if not parent.IsPseudoRoot() and not editable_prim(parent, children=True):
        raise ValueError("This parent cannot contain editable children.")
    index = 1
    destination = source.GetParentPath().AppendChild(f"{prim.GetName()}_{index}")
    while stage.GetPrimAtPath(destination):
        index += 1
        destination = source.GetParentPath().AppendChild(f"{prim.GetName()}_{index}")
    target = stage.GetEditTarget().GetLayer()
    if not target.permissionToEdit:
        raise ValueError("The edit target does not permit editing.")

    if mode == "instance":
        result = stage.DefinePrim(destination)
        result.GetReferences().AddInternalReference(source)
        result.SetInstanceable(True)
        # Instance placement belongs to the new prim. Geometry/shading remain
        # shared, while moving the source should not move the new instance.
        if UsdGeom.Xformable(prim):
            attrs = [prim.GetAttribute("xformOpOrder")]
            attrs += [attr for attr in prim.GetAttributes() if attr.GetName().startswith("xformOp:")]
            for attr in attrs:
                if not attr:
                    continue
                copied = result.CreateAttribute(attr.GetName(), attr.GetTypeName(), attr.IsCustom(), attr.GetVariability())
                for time in [Usd.TimeCode.Default(), *map(Usd.TimeCode, attr.GetTimeSamples())]:
                    value = attr.Get(time)
                    if attr.GetName() == "xformOpOrder" and time.IsDefault() and value is None:
                        value = []
                    if value is not None:
                        copied.Set(value, time)
    else:
        local = set(stage.GetLayerStack())
        # Copy every local opinion at its original strength, including specs in
        # selected ancestor variants. CopySpec also remaps internal connections,
        # relationship targets, inherits and references to the new subtree.
        specs = {(spec.layer, spec.path) for spec in prim.GetPrimStack()
                 if spec.layer in local and spec.path.IsPrimPath()}
        if any(not layer.permissionToEdit for layer, _ in specs):
            raise ValueError("A source layer does not permit editing. Choose a writable layer stack before duplicating this prim.")
        with Sdf.ChangeBlock():
            for layer, spec_path in specs:
                new_path = spec_path.GetParentPath().AppendChild(destination.name)
                Sdf.CreatePrimInLayer(layer, new_path.GetParentPath())
                if not Sdf.CopySpec(layer, spec_path, layer, new_path):
                    raise ValueError("USD could not duplicate " + str(source))
                layer.GetPrimAtPath(new_path).instanceable = False
        result = stage.DefinePrim(destination, prim.GetTypeName())
        # A child supplied by a reference/payload on an ancestor has no local
        # definition to copy. Reference that same asset's child, preserving the
        # asset's own layers and arcs, then retain the copied local overrides.
        external_child = False
        for arc in Usd.PrimCompositionQuery(prim).GetCompositionArcs():
            if not arc.IsAncestral() or not arc.IsIntroducedInRootLayerStack():
                continue
            node = arc.GetTargetNode()
            kind = arc.GetArcType()
            if kind in (Pcp.ArcTypeReference, Pcp.ArcTypePayload):
                external_child = True
                asset = node.layerStack.identifier.rootLayer.identifier
                offset = stage.GetEditTarget().GetMapFunction().timeOffset.GetInverse() * node.mapToRoot.timeOffset
                if kind == Pcp.ArcTypeReference:
                    result.GetReferences().AddReference(asset, node.path, offset, Usd.ListPositionBackOfAppendList)
                else:
                    result.GetPayloads().AddPayload(asset, node.path, offset, Usd.ListPositionBackOfAppendList)
            elif kind == Pcp.ArcTypeInherit:
                result.GetInherits().AddInherit(node.path, Usd.ListPositionBackOfAppendList)
            elif kind == Pcp.ArcTypeSpecialize:
                result.GetSpecializes().AddSpecialize(node.path, Usd.ListPositionBackOfAppendList)
        result.SetInstanceable(False)
        if external_child:
            # Re-rooting an external asset at a child narrows its reference
            # namespace. Keep links to materials and other prims outside that
            # child pointing at their existing composed stage paths.
            for child in Usd.PrimRange(prim):
                copied = stage.GetPrimAtPath(child.GetPath().ReplacePrefix(source, destination))
                for rel in child.GetRelationships():
                    paths = rel.GetTargets()
                    if any(not p.HasPrefix(source) for p in paths):
                        copied.CreateRelationship(rel.GetName(), rel.IsCustom()).SetTargets(
                            [p.ReplacePrefix(source, destination) for p in paths])
                for attr in child.GetAttributes():
                    paths = attr.GetConnections()
                    if any(not p.HasPrefix(source) for p in paths):
                        copied.CreateAttribute(attr.GetName(), attr.GetTypeName(), attr.IsCustom(), attr.GetVariability()).SetConnections(
                            [p.ReplacePrefix(source, destination) for p in paths])

    original = stage.GetLoadRules()
    rules = Usd.StageLoadRules()
    rules.SetRules(original.GetRules() + [(p.ReplacePrefix(source, destination), rule)
                                         for p, rule in original.GetRules() if p.HasPrefix(source)])
    rules.AddRule(destination, original.GetEffectiveRuleForPath(source))
    rules.Minimize()
    stage.SetLoadRules(rules)
    return str(destination)
