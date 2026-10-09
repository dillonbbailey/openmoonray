"""Channel/layer selection shared by the Qt host and native EXR worker."""

ALIASES = dict(RED="R", GREEN="G", BLUE="B", ALPHA="A")


def channel_inventory(metadata):
    layers, scalars = [], []
    parts = metadata["parts"]
    hints = {(view["part"], tuple(view["channels"])): view.get("display", "raw")
             for view in metadata.get("views", [])}
    for part in parts:
        groups = {}
        bare = {name.upper() for name in part["channels"] if "." not in name}
        for index, name in enumerate(part["channels"]):
            prefix, _, leaf = name.rpartition(".")
            component = ALIASES.get(leaf.upper(), leaf.upper())
            if not prefix:
                prefix = "rgba" if component in ("R", "G", "B", "A") else (
                    "vector" if component in ("X", "Y", "Z") and {"X", "Y"} <= bare else name)
            # Retain exact names as identity; aliases only determine RGB routing.
            groups.setdefault(prefix, []).append(dict(name=name, component=component, index=index, leaf=leaf))
            scalars.append(dict(key=[part["index"], name], part=part["index"], channel=index, name=name,
                                label=(part["name"] + " / " if len(parts) > 1 else "") + name))
        for name, members in groups.items():
            components = {m["component"]: m["index"] for m in members}
            axes = next((axes for axes in (("R", "G", "B"), ("X", "Y", "Z"), ("U", "V"))
                         if all(axis in components for axis in axes)), None)
            channels = [components[axis] for axis in axes] if axes else [members[0]["index"]]
            routes = dict(zip(("R", "G", "B"), channels)) if axes else {}
            if not axes:
                routes.update({key: value for key, value in components.items() if key in ("R", "G", "B")})
            # Alpha-only and arbitrary scalar layers display in grayscale.
            hint = hints.get((part["index"], tuple(channels)), "raw")
            if axes and axes[0] == "R" and (part["index"], tuple(channels)) not in hints:
                hint = "srgb"
            label = (part["name"] + " / " if len(parts) > 1 else "") + name
            layers.append(dict(key=[part["index"], name], part=part["index"], label=label, name=name,
                               channels=channels, routes=routes, members=members, display=hint))
    layers.sort(key=lambda layer: (layer["part"], layer["name"] != "rgba"))
    return layers, scalars


def resolve_selection(metadata, selection=None):
    """Resolve stable names anew on each checkpoint, never saved channel offsets."""
    selection = selection or {}
    layers, scalars = channel_inventory(metadata)
    if not layers:
        raise ValueError("The EXR has no channels to display.")
    layer = next((layer for layer in layers if layer["key"] == selection.get("layer")), layers[0])
    component = selection.get("component", "RGB")
    matte_key = selection.get("matte", "auto")
    if matte_key != "auto" and not any(s["key"] == matte_key for s in scalars):
        matte_key = "auto"
    if matte_key == "auto":
        alpha = next((m for m in layer["members"] if m["component"] == "A"), None)
        if alpha:
            matte = next(s for s in scalars if s["key"] == [layer["part"], alpha["name"]])
        else:
            matte = next((s for s in scalars if s["part"] == layer["part"] and
                          s["name"].lower() in ("a", "alpha", "rgba.a", "rgba.alpha")), None)
    else:
        matte = next((s for s in scalars if s["key"] == matte_key), None)
    view = dict(part=layer["part"], channels=layer["channels"], display=layer["display"])
    if component in layer["routes"]:
        view.update(channels=[layer["routes"][component]], display="raw")
    elif component not in ("RGB", "A"):
        member = next((m for m in layer["members"] if "channel:" + m["name"] == component), None)
        if member:
            view.update(channels=[member["index"]], display="raw")
        else:
            component = "RGB"
    if component == "A":
        view["display"] = "raw"
    return dict(layer=layer, view=view, matte=matte, component=component,
                overlay=bool(selection.get("overlay")) and component != "A")
