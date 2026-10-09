"""Applied USD API schema discovery, including the editor's MoonRay schemas."""
from functools import lru_cache

from pxr import Plug, Sdf, Tf, Usd

from .model import EDITOR_ROOT

PLUGIN_DIR = EDITOR_ROOT / "assets" / "usd_schemas"
MOONRAY_PLUGIN = "moonrayStudioSchemas"


def register_schemas():
    # Register before constructing a stage or initializing SchemaRegistry.
    # Maya: hdMoonray's own schemas (hdMoonraySchemas) are loaded there and
    # also define the MoonrayMeshLight alias; registering both clashes.
    if Plug.Registry().GetPluginWithName("hdMoonraySchemas"):
        return
    Plug.Registry().RegisterPlugins(str(PLUGIN_DIR / "plugInfo.json"))


register_schemas()


@lru_cache(maxsize=1)
def schema_catalog():
    registry = Usd.SchemaRegistry()
    entries = []
    for schema_type in Tf.Type.FindByName("UsdAPISchemaBase").GetAllDerivedTypes():
        if not registry.IsAppliedAPISchema(schema_type):
            continue
        name = str(registry.GetSchemaTypeName(schema_type))
        plugin = Plug.Registry().GetPluginForType(schema_type)
        definition = registry.FindAppliedAPIPrimDefinition(name)
        if not name or not definition:
            continue
        moonray = plugin.name == MOONRAY_PLUGIN or "moonray" in plugin.name.lower()
        entries.append(dict(name=name, group="MoonRay" if moonray else "USD", plugin=plugin.name,
                            multiple=registry.IsMultipleApplyAPISchema(schema_type),
                            label=name.replace("MoonrayStudio", "") if plugin.name == MOONRAY_PLUGIN else name,
                            documentation=definition.GetDocumentation()))
    return sorted(entries, key=lambda entry: (entry["group"] != "MoonRay", entry["label"].casefold()))


def schema_choices(prim):
    if not prim or prim.IsPseudoRoot() or not prim.IsActive() or prim.IsInstanceProxy() or prim.IsInPrototype():
        return []
    applied = set(prim.GetAppliedSchemas())
    result = []
    for entry in schema_catalog():
        entry = dict(entry)
        # Multiple-apply restrictions may depend on the instance name. Validate
        # those at Apply time after the user supplies the name.
        can_apply = True if entry["multiple"] else prim.CanApplyAPI(entry["name"])
        entry.update(applied=entry["name"] in applied, enabled=bool(can_apply),
                     reason=getattr(can_apply, "whyNot", ""))
        if entry["multiple"]:
            entry["instances"] = sorted(name.split(":", 1)[1] for name in applied if name.startswith(entry["name"] + ":"))
        result.append(entry)
    return result


def apply_schema(prim, name, instance=""):
    entry = next((entry for entry in schema_catalog() if entry["name"] == name), None)
    if not entry:
        raise ValueError("Unknown or non-applied USD API schema: " + name)
    if entry["multiple"]:
        if not isinstance(instance, str) or not Sdf.Path.IsValidNamespacedIdentifier(instance):
            raise ValueError("This schema requires a valid instance name, such as render or myCollection.")
        if not Usd.SchemaRegistry.IsAllowedAPISchemaInstanceName(name, instance):
            raise ValueError("This instance name is not allowed for " + name)
        args = (name, instance)
    else:
        if instance:
            raise ValueError("This schema does not use instance names.")
        args = (name,)
    can_apply = prim.CanApplyAPI(*args)
    if not can_apply:
        raise ValueError(can_apply.whyNot or "This schema cannot be applied to the selected prim.")
    if prim.HasAPI(*args):
        return
    if entry["plugin"] == MOONRAY_PLUGIN:
        definition = Usd.SchemaRegistry().FindAppliedAPIPrimDefinition(name)
        # Author actual values for Hydra and for files opened without this UI's
        # codeless plugin. Existing opinions (including animation) stay intact.
        for prop_name in definition.GetPropertyNames():
            spec = definition.GetSchemaPropertySpec(prop_name)
            existing = prim.GetAttribute(prop_name)
            if existing and existing.GetTypeName() != spec.typeName:
                raise ValueError(f"{prop_name} already has a different USD type.")
    if not prim.ApplyAPI(*args):
        raise ValueError("USD could not apply " + name)
    if entry["plugin"] == MOONRAY_PLUGIN:
        for prop_name in definition.GetPropertyNames():
            spec = definition.GetSchemaPropertySpec(prop_name)
            attr = prim.GetAttribute(prop_name)
            if attr.HasAuthoredValueOpinion() or attr.HasAuthoredConnections():
                continue
            attr = prim.CreateAttribute(prop_name, spec.typeName, custom=False, variability=spec.variability)
            if spec.HasDefaultValue() and not attr.Set(spec.default):
                raise ValueError("Could not initialize " + prop_name)
            if prop_name.startswith("primvars:"):
                attr.SetMetadata("interpolation", "constant")
            if spec.HasInfo("allowedTokens"):
                attr.SetMetadata("allowedTokens", spec.GetInfo("allowedTokens"))
