"""Read texture dependencies from the composed stage in the native USD worker."""
import os
from pathlib import PurePosixPath

from pxr import Ar, Sdf, Usd, UsdShade

from .map_preview import IMAGE_SUFFIXES
from .usd_hierarchy import PREDICATE
from .usd_project import anchor_asset


VALUE_TYPES = {Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray,
               Sdf.ValueTypeNames.String, Sdf.ValueTypeNames.StringArray,
               Sdf.ValueTypeNames.Token, Sdf.ValueTypeNames.TokenArray}
TEXTURE_SUFFIXES = IMAGE_SUFFIXES | {".ktx", ".ktx2", ".ptex", ".ptx"}


def filename(path):
    if Ar.IsPackageRelativePath(path):
        path = Ar.SplitPackageRelativePathInner(path)[1]
    return PurePosixPath(path.replace("\\", "/")).name


def texture_records(stage):
    """Unique texture paths, including missing files, UDIMs and time samples.

    Read only composed values: current variants and loaded payloads. Resolve
    relative paths against the layer supplying each value, including references.
    """
    records = {}
    with Ar.ResolverContextBinder(stage.GetPathResolverContext()):
        for prim in Usd.PrimRange.Stage(stage, PREDICATE):
            # Render destinations are image files too, but are not textures.
            shader = UsdShade.Shader(prim)
            if prim.GetTypeName() in {"RenderProduct", "RenderSettings", "RenderVar"} or (
                    shader and shader.GetIdAttr().Get() == "RenderOutput"):
                continue
            for attr in prim.GetAuthoredAttributes():
                if attr.GetTypeName() not in VALUE_TYPES or attr.GetName().startswith("outputs:"):
                    continue
                for time in [Usd.TimeCode.Default(), *map(Usd.TimeCode, attr.GetTimeSamples())]:
                    value = attr.Get(time)
                    if value is None:
                        continue
                    values = [value] if isinstance(value, (str, Sdf.AssetPath)) else value
                    for value in values:
                        authored = value.path if isinstance(value, Sdf.AssetPath) else value
                        if not authored:
                            continue
                        suffix = PurePosixPath(filename(authored)).suffix.lower()
                        texture_input = attr.GetBaseName().lower() in {"file", "filename", "texture"} or "texture" in attr.GetName().lower()
                        if suffix not in TEXTURE_SUFFIXES and not (
                                not suffix and texture_input and attr.GetTypeName() in {Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray}):
                            continue
                        path = value.resolvedPath if isinstance(value, Sdf.AssetPath) else ""
                        if not path:
                            path = authored if isinstance(value, Sdf.AssetPath) else os.path.expandvars(os.path.expanduser(authored))
                            for spec in attr.GetPropertyStack(time):
                                if spec.HasInfo("default") or not time.IsDefault() and spec.HasInfo("timeSamples"):
                                    path = anchor_asset(spec.layer, path)
                                    break
                            path = str(Ar.GetResolver().Resolve(path)) or path
                        record = records.setdefault(path, dict(path=path, name=filename(path), authored_paths=set(), usages=set()))
                        record["authored_paths"].add(authored)
                        record["usages"].add(str(attr.GetPath()))
    return [dict(record, authored_paths=sorted(record["authored_paths"]), usages=sorted(record["usages"]))
            for record in sorted(records.values(), key=lambda row: (row["name"].casefold(), row["path"]))]


class TextureInventory:
    def __init__(self, stage):
        self.stage = stage
        self.dirty = True
        self.cached = []

    def changed(self, notice):
        if notice.GetResyncedPaths():
            self.dirty = True
        else:
            for path in notice.GetChangedInfoOnlyPaths():
                attr = self.stage.GetAttributeAtPath(path) if path.IsPropertyPath() else None
                if attr and attr.GetTypeName() in VALUE_TYPES:
                    self.dirty = True
                    break
        return self.dirty

    def records(self):
        if self.dirty:
            self.cached = texture_records(self.stage)
            self.dirty = False
        return self.cached
