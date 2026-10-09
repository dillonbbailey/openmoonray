"""Read-only stage traversal and ancestor-preserving hierarchy filters."""
from pxr import Kind, Pcp, Sdf, Usd


PREDICATE = Usd.TraverseInstanceProxies(Usd.PrimIsDefined & ~Usd.PrimIsAbstract)
ABSTRACT_PREDICATE = Usd.TraverseInstanceProxies(Usd.PrimIsDefined)


def hierarchy_children(prim, show_abstract=False):
    # Keep inactive prims and unloaded payload roots available for reactivation
    # and loading. USD itself prunes the children of inactive prims.
    return prim.GetFilteredChildren(ABSTRACT_PREDICATE if show_abstract else PREDICATE)


def composition_arcs(prim):
    """Arcs introduced on this prim, excluding arcs inherited from ancestors."""
    names = {Pcp.ArcTypeReference: "reference", Pcp.ArcTypePayload: "payload",
             Pcp.ArcTypeInherit: "inherit", Pcp.ArcTypeSpecialize: "specialize",
             Pcp.ArcTypeVariant: "variant", Pcp.ArcTypeRelocate: "relocate"}
    query = Usd.PrimCompositionQuery(prim)
    query_filter = Usd.PrimCompositionQuery.Filter()
    query_filter.dependencyTypeFilter = Usd.PrimCompositionQuery.DependencyTypeFilter.Direct
    query.filter = query_filter
    arcs = {names[arc.GetArcType()] for arc in query.GetCompositionArcs() if arc.GetArcType() in names}
    # Authored arcs remain useful markers when payloads are unloaded, variants
    # have no selection yet, or a reference cannot currently resolve.
    for name, authored in (("reference", prim.HasAuthoredReferences()), ("payload", prim.HasPayload()),
                           ("inherit", prim.HasAuthoredInherits()), ("specialize", prim.HasAuthoredSpecializes()),
                           ("variant", bool(prim.GetVariantSets().GetNames()))):
        if authored:
            arcs.add(name)
    return sorted(arcs)


class StageHierarchy:
    def __init__(self, stage):
        self.stage = stage
        self.request = 0
        self.prim_type = self.prim_kind = None
        self.show_abstract = False
        self.rows = None
        self.visible = None
        self.ancestors = set()
        self.matches = set()
        self.resynced = set()
        self.changed_prims = set()
        self.index_dirty = set()
        self.reported_visible = set()

    def changed(self, notice):
        # Property resyncs (including material bindings and shader inputs) do
        # not change prim rows. Keep namespace changes separate from values.
        paths = {path for path in notice.GetResyncedPaths() if path.IsPrimPath() or path == Sdf.Path.absoluteRootPath}
        self.resynced.update(paths)
        self.index_dirty.update(paths)
        for path in [*notice.GetResyncedPaths(), *notice.GetChangedInfoOnlyPaths()]:
            if path.IsPropertyPath() and path.name == "joints":
                self.resynced.add(path.GetPrimPath())
        for path in notice.GetChangedInfoOnlyPaths():
            if path.IsPrimPath() or path == Sdf.Path.absoluteRootPath:
                self.changed_prims.add(path)
                if {"kind", "typeName"}.intersection(notice.GetChangedFields(path)):
                    self.index_dirty.add(path)

    def set_filter(self, prim_type, prim_kind, request, *, show_abstract=False):
        if any(value is not None and not isinstance(value, str) for value in (prim_type, prim_kind)):
            raise ValueError("Choose a prim type or kind from the hierarchy filters.")
        if type(show_abstract) is not bool:
            raise ValueError("Show abstract prims must be on or off.")
        if self.show_abstract != show_abstract:
            self.rows = None
        self.show_abstract = show_abstract
        self.prim_type, self.prim_kind, self.request = prim_type, prim_kind, request
        self.visible = None

    def refresh(self):
        predicate = ABSTRACT_PREDICATE if self.show_abstract else PREDICATE
        if self.rows is None:
            self.rows = {prim.GetPath(): (prim.GetTypeName(), str(prim.GetMetadata("kind") or ""))
                         for prim in Usd.PrimRange.Stage(self.stage, predicate)}
            self.visible = None
        elif self.index_dirty:
            roots = [path for path in self.index_dirty
                     if not any(path != other and path.HasPrefix(other) for other in self.index_dirty)]
            for path in list(self.rows):
                if any(path.HasPrefix(root) for root in roots):
                    del self.rows[path]
            for path in roots:
                prim = self.stage.GetPrimAtPath(path)
                if prim:
                    prims = Usd.PrimRange.Stage(self.stage, predicate) if prim.IsPseudoRoot() else Usd.PrimRange(prim, predicate)
                    self.rows.update((p.GetPath(), (p.GetTypeName(), str(p.GetMetadata("kind") or ""))) for p in prims)
            self.visible = None
        self.index_dirty.clear()
        if self.visible is not None:
            return
        self.matches = {path for path, (prim_type, prim_kind) in self.rows.items()
                        if (self.prim_type is None or prim_type == self.prim_type)
                        and (self.prim_kind is None or prim_kind == self.prim_kind
                             or bool(prim_kind and self.prim_kind and Kind.Registry.IsA(prim_kind, self.prim_kind)))}
        self.ancestors = set()
        for path in self.matches:
            parent = path.GetParentPath()
            while parent != Sdf.Path.absoluteRootPath and parent not in self.ancestors:
                self.ancestors.add(parent)
                parent = parent.GetParentPath()
        self.visible = self.matches | self.ancestors | {Sdf.Path.absoluteRootPath}

    def take_updates(self, reported):
        """Return only previously requested child lists affected by notices."""
        self.refresh()
        candidates = set()
        for path in self.resynced | self.changed_prims:
            candidates.add(path)
            candidates.update(path.GetPrefixes()[:-1])
            candidates.add(Sdf.Path.absoluteRootPath)
        candidates.update(Sdf.Path(path) for path in reported
                          if any(Sdf.Path(path).HasPrefix(root) for root in self.resynced))
        if self.prim_type is not None or self.prim_kind is not None:
            candidates.update(path.GetParentPath() for path in self.visible ^ self.reported_visible)
        self.reported_visible = set(self.visible)
        self.resynced.clear()
        self.changed_prims.clear()
        return sorted((str(path) for path in candidates if str(path) in reported), key=lambda path: (path.count("/"), path))

    def children(self, prim):
        if self.prim_type is None and self.prim_kind is None:
            return hierarchy_children(prim, self.show_abstract)
        self.refresh()
        return [child for child in hierarchy_children(prim, self.show_abstract) if child.GetPath() in self.visible]

    def info(self):
        self.refresh()
        kinds = {kind for _, kind in self.rows.values()}
        authored_kinds = tuple(kinds)
        kinds.update(kind for kind in Kind.Registry.GetAllKinds()
                     if any(value and Kind.Registry.IsA(value, kind) for value in authored_kinds))
        return dict(request=self.request, prim_type=self.prim_type, prim_kind=self.prim_kind,
                    show_abstract=self.show_abstract,
                    types=sorted({value for value, _ in self.rows.values()}), kinds=sorted(kinds),
                    matches=len(self.matches), total=len(self.rows),
                    ancestors=sorted(str(path) for path in self.ancestors)
                    if self.prim_type is not None or self.prim_kind is not None else [])
