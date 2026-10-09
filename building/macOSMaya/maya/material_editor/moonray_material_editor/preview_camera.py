"""Portable camera state shared by native previews and the USD sync controller."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path

from .model import GraphError, PROJECTION_CAMERAS


def default_values():
    length = math.hypot(.63, 4.8)
    y, z = .63 / length, 4.8 / length
    return dict(node_xform=[[1., 0., 0., 0.], [0., z, -y, 0.], [0., y, z, 0.], [0., .55, 4.8, 1.]],
                focal=50., film_width_aperture=30.)


def snapshot(graph, base_dir):
    node = graph.node(graph.data["camera"]) if graph.data["camera"] else None
    kind = node["shader"] if node else "PreviewCamera"
    defaults = kind == "PreviewCamera"
    kind = "PerspectiveCamera" if defaults else kind
    if kind not in PROJECTION_CAMERAS:
        raise GraphError("The linked USD preview camera supports PerspectiveCamera and OrthographicCamera.")
    attrs = graph.catalog.attributes(kind)
    values = {name: copy.deepcopy(attr.get("default")) for name, attr in attrs.items()
              if not attr["attrType"].startswith("SceneObject")}
    values.update(default_values() if defaults else copy.deepcopy(node["values"]))
    for name, value in values.items():
        if attrs[name].get("filename") and value and "://" not in value:
            path = Path(os.path.expandvars(value)).expanduser()
            values[name] = str(path if path.is_absolute() else Path(base_dir) / path)
    return dict(material_id=graph.data["material_id"], shader=kind, values=values)


def signature(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
