"""Light graph snapshots and renderer-evaluated color textures."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .map_preview import absolute_asset, map_snapshot
from .model import Graph

LIGHT_FIELDS = ("color", "intensity", "exposure", "normalized", "texture", "width", "height",
                "radius", "length", "lens_radius", "outer_cone_angle", "inner_cone_angle",
                "angular_extent", "spread", "sidedness", "visible_in_camera")
LIGHT_CLASSES = {"RectLight": "RectLight", "DomeLight": "EnvLight", "SphereLight": "SphereLight",
                 "DistantLight": "DistantLight", "DiskLight": "DiskLight", "CylinderLight": "CylinderLight",
                 "MoonrayMeshLight": "MeshLight"}


def light_fields(kind):
    return tuple(name for name in LIGHT_FIELDS if name != "texture" or kind not in {"MeshLight", "DistantLight"})


def light_snapshot(graph, node_id, base):
    node = graph.node(node_id)
    if graph.catalog.category(node["shader"]) != "Light":
        raise ValueError("Select a light node.")
    needed, pending = set(), [node_id]
    while pending:
        current = pending.pop()
        if current not in needed:
            needed.add(current)
            pending.extend(c["source"] for c in graph.data["connections"] if c["target"] == current)
    result = Graph(graph.catalog)
    result.data["material_id"] = graph.data["material_id"]
    result.data["name"] = node["label"]
    result.data["nodes"] = [copy.deepcopy(n) for n in graph.data["nodes"] if n["id"] in needed]
    result.data["connections"] = [copy.deepcopy(c) for c in graph.data["connections"] if c["target"] in needed]
    result.data["light"] = node_id
    for item in result.data["nodes"]:
        for name, attr in graph.catalog.attributes(item["shader"]).items():
            value = graph.value(item, name)
            if attr.get("filename") and value and "://" not in value:
                item["values"][name] = str(absolute_asset(value, base))
    result.validate()
    return result


def bake_light_color(graph, node_id, directory, size=512):
    """Return a persistent, linear EXR for a connected color, or the image path."""
    node = graph.node(node_id)
    connection = graph.incoming(node_id, "color")
    if not connection:
        return graph.value(node, "texture") if "texture" in graph.catalog.attributes(node["shader"]) else ""
    from .map_preview import bake_map
    key, payload = map_snapshot(graph, connection["source"])
    channel = connection.get("output", "out")
    key = hashlib.sha256(f"light-map-v1:{key}:{channel}:{size}".encode()).hexdigest()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (key + ".exr")
    if not destination.is_file():
        with tempfile.TemporaryDirectory(prefix=".light-bake-", dir=directory) as scratch:
            output = bake_map(dict(payload, output=channel), Path(scratch), size=size)
            temporary = Path(scratch) / "light.exr"
            shutil.copy2(output, temporary)
            temporary.replace(destination)
    return str(destination)


def prepare_light_maps(graph, directory):
    nodes = [n["id"] for n in graph.data["nodes"] if graph.catalog.category(n["shader"]) == "Light"
             and graph.incoming(n["id"], "color")]
    if not nodes:
        return graph
    result = graph.clone()
    for node_id in nodes:
        result.node(node_id)["values"]["texture"] = bake_light_color(graph, node_id, directory)
        result.disconnect(node_id, "color")
    return result


def light_update(graph, node_id, directory):
    node = graph.node(node_id)
    reference = node.get("usd_light")
    if not reference:
        raise ValueError("This light has no USD reference. Open a light from USD SCENE LIGHTS first.")
    unsupported = [c["input"] for c in graph.data["connections"] if c["target"] == node_id and c["input"] != "color"]
    if unsupported:
        raise ValueError("USD light references currently support connections on color only.")
    values = {name: graph.value(node, name) for name in light_fields(node["shader"]) if name in graph.catalog.attributes(node["shader"])}
    if "texture" in values:
        values["texture"] = bake_light_color(graph, node_id, directory)
        if "texture" in node.get("tx_textures", []) and values["texture"]:
            from .tx_cache import prepare_texture
            values["texture"] = prepare_texture(values["texture"])
    return dict(reference=reference, shader=node["shader"], values=values,
                graph=json.dumps(graph.data, separators=(",", ":")))
