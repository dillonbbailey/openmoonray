"""Persistent associations between editor graphs and composed USD materials."""
import copy
import json

from pxr import UsdShade

from .material_sync import signature, snapshot
from .usd_shader_graph import exported_network, fingerprint, apply_graph, material_outputs

KEY = "moonrayEditor:materialSync"


def store_link(stage, graph, link):
    record = dict(material_id=graph["material_id"], signature=signature(graph), link=copy.deepcopy(link))
    record["link"].pop("scene", None)
    stage.GetPrimAtPath(link["path"]).SetCustomDataByKey(KEY, json.dumps(record, separators=(",", ":")))
    return record


def associate(stage, material, graph, base_dir, prefix=None):
    data = snapshot(graph, base_dir)
    baseline = copy.deepcopy(data)
    outputs = material_outputs(material.GetPrim())
    for terminal in ("surface", "displacement", "volume"):
        if terminal not in outputs:
            baseline[terminal] = None
    # Ignore shaders omitted from a copied preview's active terminals.
    from .model import Graph
    baseline = snapshot(Graph(graph.catalog, baseline), base_dir)
    exported, shaders, _ = exported_network(baseline)
    paths = {}
    if prefix:
        from .shader_adapters import native_graph
        native = native_graph(graph)
        names = native.export_names()
        channels = {}
        for c in native.data["connections"]:
            channel = (c["source"], c.get("output", "out"))
            if channel[1] != "out" and channel not in channels:
                channels[channel] = "/PreviewScene/Channels/graph_channel_" + str(len(channels))
    for key, shader in shaders.items():
        if prefix:
            node_id = shader.GetPrim().GetCustomDataByKey("editor:nodeId")
            if node_id:
                path = prefix + names[node_id]
            else:
                channel = (shader.GetPrim().GetCustomDataByKey("editor:channelSource"),
                           shader.GetPrim().GetCustomDataByKey("editor:channel"))
                path = channels[channel]
        else:
            path = str(material.GetPath()) + "/" + shader.GetPrim().GetName()
        if not UsdShade.Shader(stage.GetPrimAtPath(path)):
            raise ValueError("Cannot link missing USD shader " + path)
        paths[key] = path
    link = dict(path=str(material.GetPath()), frame=0, paths=paths, baseline=baseline, interfaces=[],
                outputs={key: attr.GetName().removeprefix("outputs:") for key, attr in outputs.items()})
    link["fingerprint"] = fingerprint(stage, link)
    return store_link(stage, data, link)


def records(stage, scene, frame):
    result = []
    for prim in stage.Traverse():
        stored = prim.GetCustomDataByKey(KEY)
        if not stored:
            continue
        try:
            record = json.loads(stored)
            link = record["link"]
            if link["path"] != str(prim.GetPath()):
                continue
            link.update(scene=scene, frame=frame)
            result.append(record)
        except (ValueError, KeyError, TypeError):
            continue
    return result


def apply_updates(stage, updates):
    result = []
    for data in updates:
        link = apply_graph(stage, data)
        record = store_link(stage, data["graph"], link)
        record["link"]["scene"] = data["link"]["scene"]
        result.append(record)
    return result
