"""Portable material snapshots and identities shared by both Qt processes."""
import hashlib
import json
import os
from pathlib import Path

from .model import Graph, SHADER_TYPES, TERMINALS, PROJECTION_CAMERAS


def snapshot(graph, base_dir):
    result = graph.clone()
    needed = set()
    pending = [result.data[t] for t in ("surface", "displacement", "volume") if result.data[t]]
    if not pending:
        pending = [n["id"] for n in result.data["nodes"] if result.catalog.definition(n["shader"])["type"] in SHADER_TYPES]
    while pending:
        node = pending.pop()
        if node not in needed:
            needed.add(node)
            pending.extend(c["source"] for c in result.data["connections"] if c["target"] == node)
    result.data["nodes"] = [n for n in result.data["nodes"] if n["id"] in needed]
    result.data["connections"] = [c for c in result.data["connections"] if c["target"] in needed]
    for terminal in TERMINALS:
        if terminal not in ("surface", "displacement", "volume"):
            result.data[terminal] = None
    for node in result.data["nodes"]:
        if result.catalog.definition(node["shader"])["type"] not in SHADER_TYPES and node["shader"] not in PROJECTION_CAMERAS:
            raise ValueError("This material depends on scene objects that cannot be synchronized as USD shaders.")
        for name, value in node["values"].items():
            if result.catalog.attributes(node["shader"])[name].get("filename") and value and "://" not in value:
                path = Path(os.path.expandvars(value)).expanduser()
                node["values"][name] = str(path if path.is_absolute() else Path(base_dir) / path)
    result.validate()
    return result.data


def signature(data):
    """Layout, exposed sockets and preview settings do not change USD shaders."""
    values = {key: data.get(key) for key in ("material_id", "name", "surface", "displacement", "volume", "connections")}
    values["nodes"] = [{key: node[key] for key in ("id", "shader", "label", "values", "usd_camera", "usd_transform") if key in node}
                       for node in data["nodes"]]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
