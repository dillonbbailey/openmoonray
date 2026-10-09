"""Native per-channel color-correction parameters (no shader schema changes)."""


def channel_groups(catalog, shader):
    attrs = catalog.attributes(shader)
    groups = {}
    for base in ("saturation", "contrast", "gamma", "gain", "offset"):
        channels = tuple(base + "_" + channel for channel in "rgb")
        switch = "use_per_channel_" + ("gain_offset" if base in ("gain", "offset") else base)
        if (attrs.get(switch, {}).get("attrType") == "Bool" and
                all(attrs.get(name, {}).get("attrType") == "Float" for name in (base, *channels))):
            groups[base] = dict(channels=channels, switch=switch)
    return groups


def is_rgb_grade(shader, name, attr):
    return (attr["attrType"] == "Rgb" and shader in {"ImageMap", "ColorCorrectDisplayFilter"}
            and name in {"saturation", "contrast", "gamma_adjust", "gain", "offset_adjust", "multiply", "offset"})


def set_channel_mode(graph, node_id, switch, enabled):
    node = graph.node(node_id)
    # On first use, seed RGB from the uniform control. In particular, the
    # renderer defaults offset_r/g/b to 1 while uniform offset defaults to 0.
    # Re-enabling existing per-channel edits must retain those authored values.
    if enabled and not graph.value(node, switch):
        for base, group in channel_groups(graph.catalog, node["shader"]).items():
            if group["switch"] == switch and not any(
                    name in node["values"] or graph.incoming(node_id, name) for name in group["channels"]):
                for name in group["channels"]:
                    graph.set_value(node_id, name, graph.value(node, base))
    graph.set_value(node_id, switch, enabled)
