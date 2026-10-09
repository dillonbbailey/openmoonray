"""Translate editor conveniences to supported native MoonRay shader networks."""
import copy


def native_graph(graph):
    """Lower connected Checkerboard colors without changing the editable graph.

    Keep the user's node ID/name on the resulting BlendMap so all downstream
    connections, including channel adapters, read the combined color. The extra
    CheckerboardMap only supplies a black/white mask with the original UV setup.
    """
    connected = {c["target"] for c in graph.data["connections"] if c["input"] in {"color_A", "color_B"}}
    checkers = [n["id"] for n in graph.data["nodes"] if n["shader"] == "CheckerboardMap" and n["id"] in connected]
    if not checkers:
        return graph
    result = graph.clone()
    names = result.export_names()
    used_names = set(names.values())
    used_ids = {n["id"] for n in result.data["nodes"]}
    for node_id in checkers:
        node = result.node(node_id)
        mask = copy.deepcopy(node)
        mask_id = node_id + "_checker_mask"
        while mask_id in used_ids:
            mask_id += "_"
        used_ids.add(mask_id)
        # Reserve existing exported names, including names created for duplicates.
        base = names[node_id][:100] + "_checker_mask"
        mask_name, suffix = base, 2
        while mask_name in used_names:
            mask_name = f"{base}_{suffix}"
            suffix += 1
        used_names.add(mask_name)
        mask.update(id=mask_id, label=mask_name, ports=["input_texture_coordinates"])
        mask["values"].update(color_A=[0.0, 0.0, 0.0], color_B=[1.0, 1.0, 1.0])
        colors = {key: ([1.0, 1.0, 1.0] if result.incoming(node_id, key) else result.value(node, key))
                  for key in ("color_A", "color_B")}
        # A unit binding factor ensures the mask covers 0..1, and connected
        # colors replace even the default black A without multiplying it away.
        node.update(shader="BlendMap", values={**colors, "blend_amount": 1.0},
                    ports=["color_A", "color_B", "blend_amount"])
        node.pop("port_version", None)
        result.data["nodes"].append(mask)
        for connection in result.data["connections"]:
            if connection["target"] == node_id and connection["input"] == "input_texture_coordinates":
                connection["target"] = mask_id
        result.data["connections"].append(dict(source=mask_id, target=node_id, input="blend_amount", output="out"))
    result.validate()
    return result
