"""Portable node selections; clipboard data never includes scene output assignments."""
import copy
import json
import os
from pathlib import Path
import uuid

from .model import Graph, GraphError, validate_value

MIME_TYPE = "application/x-lunatic-shader-nodes+json"


def encode_nodes(graph, node_ids, base_dir):
    selected = set(node_ids)
    nodes = [node for node in graph.data["nodes"] if node["id"] in selected]
    if not nodes:
        return None
    payload = {"version": 1, "base_dir": str(Path(base_dir).resolve()), "nodes": nodes,
               "connections": [edge for edge in graph.data["connections"]
                               if edge["source"] in selected and edge["target"] in selected]}
    return json.dumps(payload, allow_nan=False).encode("utf-8")


def paste_nodes(graph, encoded, position, base_dir):
    """Validate and append fresh instances atomically, returning their new IDs."""
    try:
        payload = json.loads(encoded)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise GraphError("Unsupported node clipboard format")
        source_dir = payload["base_dir"]
        if not isinstance(source_dir, str) or not Path(source_dir).is_absolute():
            raise GraphError("Invalid node clipboard directory")
        fragment = Graph(graph.catalog)
        fragment.data.update(nodes=payload["nodes"], connections=payload["connections"])
        fragment.validate()
        if not fragment.data["nodes"]:
            raise GraphError("The node clipboard is empty")
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
        raise GraphError(f"Cannot paste nodes: {exc}") from exc

    position = list(position)
    validate_value({"attrType": "Vec2d"}, position)
    nodes = fragment.data["nodes"]
    origin = [min(node["position"][axis] for node in nodes) for axis in (0, 1)]
    # Repeated pastes at the same pointer position remain separately visible.
    occupied = {tuple(node["position"]) for node in graph.data["nodes"]}
    while any(tuple(position[axis] + node["position"][axis] - origin[axis]
                    for axis in (0, 1)) in occupied for node in nodes):
        shifted = [value + 40 for value in position]
        if shifted == position:
            raise GraphError("Cannot offset pasted nodes at this position")
        position = shifted

    candidate = graph.clone()
    used = {node["id"] for node in candidate.data["nodes"]}
    remap = {}
    for node in nodes:
        node_id = "n_" + uuid.uuid4().hex[:12]
        while node_id in used:
            node_id = "n_" + uuid.uuid4().hex[:12]
        used.add(node_id)
        remap[node["id"]] = node_id
        node["id"] = node_id
        node["position"] = [position[axis] + node["position"][axis] - origin[axis] for axis in (0, 1)]
        if Path(source_dir) != Path(base_dir).resolve():
            attrs = graph.catalog.attributes(node["shader"])
            for name, value in node["values"].items():
                if attrs[name].get("filename"):
                    node["values"][name] = _anchor_filename(value, source_dir)
        candidate.data["nodes"].append(node)
    for edge in fragment.data["connections"]:
        edge = copy.deepcopy(edge)
        edge.update(source=remap[edge["source"]], target=remap[edge["target"]])
        candidate.data["connections"].append(edge)
    candidate.validate()
    graph.data = candidate.data
    return list(remap.values())


def _anchor_filename(value, directory):
    if isinstance(value, list):
        return [_anchor_filename(item, directory) for item in value]
    if isinstance(value, str) and value and "://" not in value:
        path = Path(os.path.expandvars(value)).expanduser()
        return str(path if path.is_absolute() else Path(directory) / path)
    return value
