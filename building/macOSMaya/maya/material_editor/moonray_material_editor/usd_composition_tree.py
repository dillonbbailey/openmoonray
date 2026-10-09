"""Presentation hierarchy for read-only USD composition diagnostics."""
from pxr import Sdf


def node_parent(index, nodes):
    """Pcp NodeRef equality is meaningful; Python wrapper hashes are not."""
    parent = nodes[index].parent
    while parent:
        for candidate, node in enumerate(nodes):
            if node == parent:
                return f"node:{candidate}"
        parent = parent.parent
    return None


def layer_tree_entries(stage, node):
    """Yield (layer, cumulative offset, occurrence ID, parent occurrence ID)."""
    if node is not None:
        stack = [(tree, None) for tree in (node.layerStack.layerTree, node.layerStack.sessionLayerTree) if tree]
        index = 0
        while stack:
            tree, parent = stack.pop()
            key = str(index)
            index += 1
            yield tree.layer, node.mapToRoot.timeOffset * tree.offset, key, parent
            stack.extend((child, key) for child in reversed(tree.childTrees))
    else:
        # The pseudo-root has no prim composition index. Walk already-composed
        # local layers without opening files or reintroducing muted layers.
        included = set(stage.GetLayerStack())
        stack = [(layer, Sdf.LayerOffset(), None, frozenset())
                 for layer in (stage.GetRootLayer(), stage.GetSessionLayer()) if layer]
        index = 0
        while stack:
            layer, offset, parent, ancestors = stack.pop()
            if layer not in included or layer.identifier in ancestors:
                continue
            key = str(index)
            index += 1
            yield layer, offset, key, parent
            ancestors = ancestors | {layer.identifier}
            children = []
            for path, child_offset in zip(layer.subLayerPaths, layer.subLayerOffsets):
                child = Sdf.Layer.FindRelativeToLayer(layer, path)
                if child:
                    children.append((child, offset * child_offset, key, ancestors))
            stack.extend(reversed(children))


class SpecGroups:
    """Show each authored spec inside its actual layer and namespace owners."""
    def __init__(self, result, layer_info, layer_details, sources, limit):
        self.result = result
        self.info, self.details, self.sources = layer_info, layer_details, sources
        self.limit = limit
        self.groups = result.setdefault("groups", [])
        self.ids = set()

    def add(self, key, parent, cells, details):
        if key not in self.ids:
            if len(self.groups) >= self.limit:
                self.result["truncated"] = True
                return parent
            self.ids.add(key)
            self.groups.append(dict(id=key, parent=parent, cells=cells, details="\n".join(details), group=True))
        return key

    def parent(self, spec):
        layer = spec.layer
        base = "layer:" + layer.identifier
        parent = self.add(base, None, [self.info(layer, self.sources)["name"]], self.details(layer, self.sources))
        # Include variant namespace components too; GetPrimAtPath can retrieve
        # their real Sdf PrimSpecs even though they are not composed UsdPrims.
        for path in spec.path.GetPrefixes()[:-1]:
            ancestor = layer.GetPrimAtPath(path)
            if ancestor:
                parent = self.add(base + ":" + str(path), parent, [str(path)],
                                  [*self.details(layer, self.sources), "Containing prim spec: " + str(path)])
        return parent
