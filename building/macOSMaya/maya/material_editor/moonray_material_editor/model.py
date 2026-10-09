"""Renderer metadata and a Qt-independent, validated shader graph document."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import uuid
from .geometry_settings import TESSELLATION_MAX
from .transform_graph import projection_matrix_mode
from .color_ramp import ramp_color_inputs

# Maya: the editor lives in openmoonray/building/macOSMaya/maya/material_editor;
# MoonRay's install and caches are in the workspace's local-build.
EDITOR_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(os.environ.get("MOONRAY_SOURCE_ROOT", EDITOR_ROOT.parents[3])).resolve()
LOCAL_ROOT = Path(os.environ.get("MOONRAY_LOCAL_ROOT", SOURCE_ROOT.parent / "local-build"))
USD_ROOT = Path(os.environ.get("MOONRAY_USD25_ROOT", LOCAL_ROOT))
CATALOG_PATH = Path(os.environ.get("MOONRAY_CLASS_PATH", LOCAL_ROOT / "install/shader_json"))
# Render jobs, previews and textures - outside the source tree.
CACHE_ROOT = Path(os.environ.get("MOONRAY_EDITOR_CACHE", LOCAL_ROOT / "maya" / "material-editor-cache"))
MATERIAL_TYPES = {"Material", "DwaBaseLayerable", "DwaBaseHairLayerable"}
SHADER_TYPES = MATERIAL_TYPES | {"Map", "NormalMap", "Displacement", "Volume"}
PROJECTION_CAMERAS = {"PerspectiveCamera", "OrthographicCamera"}
SCENE_TYPES = {"Light", "LightFilter", "DisplayFilter", "RenderOutput", "Camera", "Geometry", "LightSet", "TraceSet", "GeometrySet", "ShadowSet", "ShadowReceiverSet", "LightFilterSet", "Metadata", "UserData"}
TERMINALS = {"surface": "Material", "displacement": "Displacement", "volume": "Volume", "light": "Light", "display": "DisplayFilter", "aov": "RenderOutput", "geometry": "Geometry", "camera": "Camera", "lightset": "LightSet"}
RAY_DEPTHS = {"max_diffuse_depth": ("Diffuse depth", 2),
              "max_glossy_depth": ("Glossy depth", 2), "max_mirror_depth": ("Mirror / refraction depth", 3),
              "max_hair_depth": ("Hair depth", 10), "max_volume_depth": ("Volume depth", 1),
              "max_presence_depth": ("Presence depth", 16), "max_depth": ("Total ray depth", 10)}
LOBE_SAMPLES = {"bsdf_samples": ("BSDF / glossy samples · squared", 2),
                "bssrdf_samples": ("SSS samples · squared", 2)}
LIGHT_SAMPLES = {"light_samples": ("Light samples · squared", 2)}
LIGHT_SAMPLING = {"light_sampling_mode": ("Light sampling mode", 0),
                  "light_sampling_quality": ("Light sampling quality", 0.5), **LIGHT_SAMPLES}
PREVIEW_DEFAULTS = {"displacement_enabled": True, "subdivision_scheme": "catmullClark",
                    "shadow_terminator_fix": 0,
                    "studio_light_level": 1.0,
                    "mesh_resolution": 4, "adaptive_error": 1.0, "geometry": "auto",
                    "render_view": "beauty", "samples": 5, **{key: default for key, (_, default) in RAY_DEPTHS.items()},
                    **{key: default for key, (_, default) in {**LOBE_SAMPLES, **LIGHT_SAMPLING}.items()},
                    "vdb_file": "", "vdb_grid": "density", "vdb_scale": 1.0, "vdb_offset": [0.0, 0.0, 0.0]}
GEOMETRIES = {"auto", "sphere", "cube", "card", "cloth", "hair", "volume", "vdb", "light_rig"}
RENDER_VIEWS = {"beauty", "albedo", "roughness", "normal", "depth", "uv", "wireframe"}
SCALAR_TYPES = {"Bool", "Int", "Long", "Float", "Double", "String"}
VECTOR_TYPES = {"Rgb": 3, "Rgba": 4, "Vec2f": 2, "Vec2d": 2, "Vec3f": 3,
                "Vec3d": 3, "Vec4f": 4, "Vec4d": 4}
MATRIX_TYPES = {"Mat3f": 3, "Mat3d": 3, "Mat4f": 4, "Mat4d": 4}
COMMON_PORTS = ["albedo", "roughness", "metallic", "specular", "input_normal", "emission"]
UPGRADED_PORTS = {"CheckerboardMap": ("color_A", "color_B"), "ImageMap": ("gain", "offset_adjust"),
                  "DwaMetalMaterial": ("metallic_color", "metallic_edge_color")}
GLITTER_SPACE_FIELDS = ("glitter_space", "fallback_glitter_space")


class GraphError(ValueError):
    pass


class Catalog:
    def __init__(self, directory=CATALOG_PATH):
        self.shaders = {}
        for path in sorted(Path(directory).glob("*.json")):
            for name, definition in json.loads(path.read_text()).get("scene_classes", {}).items():
                if definition.get("type") in SHADER_TYPES | SCENE_TYPES and name != "Layer":
                    self.shaders[name] = definition
                    for attr_name, attr in (definition.get("attributes") or {}).items():
                        if attr.get("interface") == "DwaBase":
                            attr["interface"] = "DwaBaseLayerable"
                        if attr_name == "light_filters":
                            attr["interface"] = "LightFilter"
                        if definition["type"] == "DisplayFilter" and attr.get("interface") == "RenderOutput":
                            attr["interface"] = "ImageBuffer"
                    if name == "NormalDisplacement":
                        # The bundled JSON says 'added'; the native ISPC shader subtracts it.
                        definition["attributes"]["zero_value"]["metadata"]["comment"] = (
                            "Neutral height-map value. With a connected map, displacement is height × "
                            "height multiplier × (average RGB − zero value). Ignored when height has no map binding.")
                    if name == "CheckerboardMap":
                        # Native Checkerboard colors are constants. Exporters lower
                        # these editor inputs to a checker mask and a BlendMap.
                        for color in ("color_A", "color_B"):
                            attr = definition["attributes"][color]
                            attr["bindable"] = True
                            attr.setdefault("metadata", {})["comment"] = (
                                "Color of these checker squares. A connected map replaces this color.")
                    if definition["type"] == "Light" and name not in {"MeshLight", "DistantLight", "PortalLight"} and "texture" in (definition.get("attributes") or {}):
                        color = definition["attributes"].get("color")
                        if color:
                            color["bindable"] = True
                            metadata = color.setdefault("metadata", {})
                            metadata["comment"] = metadata.get("comment", "") + (
                                " A connected map is baked over 0–1 UVs into a linear light texture. "
                                "It replaces the texture file and is multiplied by this color.")
        if not self.shaders:
            raise GraphError(f"No MoonRay shader definitions found in {directory}. Build MoonRay and generate shader_json first.")
        for name, kind in (("PreviewObject", "Geometry"), ("PreviewCamera", "Camera"), ("PreviewKeyLight", "Light")):
            self.shaders[name] = {"type": kind, "attributes": {}, "preview_reference": True}

    def definition(self, shader):
        try:
            return self.shaders[shader]
        except KeyError:
            raise GraphError(f"Unknown shader: {shader}") from None

    def category(self, shader):
        kind = self.definition(shader)["type"]
        return "Material" if kind in MATERIAL_TYPES else kind

    def attributes(self, shader):
        return self.definition(shader).get("attributes") or {}

    def ordered_attributes(self, shader):
        return sorted(self.attributes(shader), key=lambda n: self.attributes(shader)[n].get("order", 999))

    def parameter_groups(self, shader, names=None):
        """Inspector groups with editor overrides, ramp members and uncategorized attributes."""
        attrs = self.attributes(shader)
        names = self.ordered_attributes(shader) if names is None else names
        remaining = dict.fromkeys(name for name in names if name in attrs)
        grouping = self.definition(shader).get("grouping") or {}
        groups = grouping.get("groups") or {}
        if not groups:
            return [("", list(remaining))]
        if self.category(shader) == "Material" and "specular" in remaining and "Specular" in groups:
            # Keep specular amount beside its enable switch instead of Advanced.
            groups = {title: [name for name in members if name != "specular"]
                      for title, members in groups.items()}
            specular = groups["Specular"]
            index = specular.index("show_specular") + 1 if "show_specular" in specular else 0
            specular.insert(index, "specular")
        structures = {}
        for name in remaining:
            structure = attrs[name].get("metadata", {}).get("structure_name")
            if structure:
                structures.setdefault(structure, []).append(name)
        result = {}
        for title in dict.fromkeys([*(grouping.get("order") or []), *groups]):
            members = []
            for entry in groups.get(title, []):
                for name in ([entry] if entry in attrs else structures.get(entry, [])):
                    if name in remaining:
                        members.append(name)
                        del remaining[name]
            if members:
                result[title] = members
        if remaining:
            if "Advanced" in groups:
                result.setdefault("Advanced", []).extend(remaining)
            else:
                result = {"General": list(remaining) + result.pop("General", []), **result}
        return list(result.items())

    def input_kind(self, attr):
        if attr["attrType"].startswith("SceneObject"):
            return attr.get("interface") or "SceneObject"
        if attr.get("bindable"):
            return "Map"
        return None

    def accepts(self, shader, attr):
        requested = self.input_kind(attr)
        actual = self.definition(shader)["type"]
        if requested == "ImageBuffer":
            return actual in {"RenderOutput", "DisplayFilter"}
        if requested == "SceneObject":
            return True
        if requested == "Node":
            return "node_xform" in self.attributes(shader) or self.definition(shader).get("preview_reference", False)
        return actual == requested or (requested == "Material" and actual in MATERIAL_TYPES)

    def outputs(self, shader):
        if self.category(shader) != "Map":
            return ["out"]
        outputs = ["out", "r", "g", "b"]
        if shader in {"ImageMap", "UsdUVTexture"}:
            outputs.append("a")
        if shader.startswith("Test"):
            return ["out"]
        return outputs

    def ports(self, shader):
        return [n for n in self.ordered_attributes(shader) if self.input_kind(self.attributes(shader)[n])]


def editable(attr):
    t = attr["attrType"]
    return t in SCALAR_TYPES or t in VECTOR_TYPES or t in MATRIX_TYPES or (
        t.endswith("Vector") and t[:-6] in SCALAR_TYPES | set(VECTOR_TYPES) | set(MATRIX_TYPES))


def validate_value(attr, value):
    t = attr["attrType"]
    if t in MATRIX_TYPES:
        size = MATRIX_TYPES[t]
        if not isinstance(value, list) or len(value) != size:
            raise GraphError(f"Expected a {size} × {size} matrix")
        for row in value:
            if not isinstance(row, list) or len(row) != size:
                raise GraphError(f"Expected a {size} × {size} matrix")
            for component in row:
                validate_value({"attrType": "Double"}, component)
        return
    if t.endswith("Vector"):
        if not isinstance(value, list):
            raise GraphError("Expected an array")
        for item in value:
            validate_value({"attrType": t[:-6]}, item)
        return
    if t in VECTOR_TYPES:
        if not isinstance(value, list) or len(value) != VECTOR_TYPES[t]:
            raise GraphError(f"Expected {VECTOR_TYPES[t]} components")
        for item in value:
            validate_value({"attrType": "Double"}, item)
        return
    valid = False
    if t == "Bool":
        valid = type(value) is bool
    elif t in {"Int", "Long"}:
        bits = 32 if t == "Int" else 64
        valid = type(value) is int and -(2 ** (bits - 1)) <= value < 2 ** (bits - 1)
    elif t in {"Float", "Double"}:
        valid = type(value) in (int, float) and math.isfinite(value)
    elif t == "String":
        valid = isinstance(value, str)
    if not valid:
        raise GraphError(f"Invalid {t} value")
    if attr.get("enum") and value not in attr["enum"].values():
        raise GraphError("Value is not one of this shader's enum choices")


def export_identifier(text, fallback, prefix):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip())
    if not re.search(r"[A-Za-z0-9]", name):
        name = fallback
    if name[0].isdigit():
        name = prefix + name
    return name[:128]


class Graph:
    def __init__(self, catalog, data=None):
        self.catalog = catalog
        self.data = copy.deepcopy(data) if data is not None else {
            "version": 3, "name": "Untitled material", "nodes": [], "connections": [],
            **{terminal: None for terminal in TERMINALS}, "preview": copy.deepcopy(PREVIEW_DEFAULTS)}
        if isinstance(self.data, dict) and self.data.get("version") in (1, 2):
            self.data["version"] = 3
            for terminal in TERMINALS:
                if terminal != "surface":
                    self.data.setdefault(terminal, None)
            self.data["preview"] = {**PREVIEW_DEFAULTS, **self.data.get("preview", {})}
            for connection in self.data.get("connections", []):
                connection.setdefault("output", "out")
        # Older version 3 documents predate the additional sampling/depth
        # controls. Preserve authored values while supplying missing defaults.
        if isinstance(self.data, dict) and self.data.get("version") == 3 and isinstance(self.data.get("preview"), dict):
            self.data["preview"].setdefault("shadow_terminator_fix", 0)
            self.data["preview"].setdefault("studio_light_level", 1.0)
            for key, (_, default) in {**RAY_DEPTHS, **LOBE_SAMPLES, **LIGHT_SAMPLING}.items():
                self.data["preview"].setdefault(key, default)
        if isinstance(self.data, dict):
            self.data.setdefault("material_id", uuid.uuid4().hex)
        self.validate()
        # Expose the newly supported colors once in older graphs, while retaining
        # subsequent user choices to hide sockets across cloning and save/reopen.
        for node in self.data["nodes"]:
            colors = ramp_color_inputs(catalog, node["shader"])
            if colors and not node.get("ramp_ports_version"):
                colors = colors[:len(self.value(node, "colors"))]
                node["ports"].extend(p for p in colors if p not in node["ports"])
                node["ramp_ports_version"] = 1
            added = UPGRADED_PORTS.get(node["shader"])
            if added and "port_version" not in node and all(p in catalog.ports(node["shader"]) for p in added):
                node["ports"] = [p for p in added if p not in node["ports"]] + node["ports"]
                node["port_version"] = 1

    def clone(self):
        return Graph(self.catalog, self.data)

    def node(self, node_id):
        for node in self.data["nodes"]:
            if node["id"] == node_id:
                return node
        raise GraphError(f"Unknown node: {node_id}")

    def add(self, shader, pos=(0, 0)):
        attrs = self.catalog.attributes(shader)
        available = self.catalog.ports(shader)
        ports = [p for p in COMMON_PORTS if p in available] if self.catalog.category(shader) == "Material" else []
        ports = ports or available[:6]
        if shader == "DwaMetalMaterial":
            ports = [*UPGRADED_PORTS[shader], *[p for p in ports if p not in UPGRADED_PORTS[shader]]]
        node = {"id": "n_" + uuid.uuid4().hex[:12], "shader": shader, "label": shader,
                "position": list(pos), "values": {}, "ports": ports}
        # New materials must work on preview meshes without rest-position data.
        # Author the editor default explicitly so exports retain it; keep the
        # native catalog default intact for existing graphs and USD imports.
        for name in GLITTER_SPACE_FIELDS:
            choices = attrs.get(name, {}).get("enum", {})
            if "object" in choices:
                node["values"][name] = choices["object"]
        colors = ramp_color_inputs(self.catalog, shader)
        if colors:
            node["ports"] = [p for p in ports if p not in colors] + colors[:len(self.value(node, "colors"))]
            node["ramp_ports_version"] = 1
        if self.catalog.category(shader) == "Map":
            for name, attr in attrs.items():
                meta = attr.get("metadata", {})
                if meta.get("structure_type") == "ramp_color" and meta.get("structure_path") == "interpolation_types":
                    node["values"][name] = [1] * len(attr.get("default", []))
        if shader in UPGRADED_PORTS and all(p in available for p in UPGRADED_PORTS[shader]):
            node["port_version"] = 1
        self.data["nodes"].append(node)
        if self.data["surface"] is None and self.catalog.category(shader) == "Material":
            self.data["surface"] = node["id"]
        return node

    def value(self, node, name):
        return node["values"].get(name, self.catalog.attributes(node["shader"])[name].get("default"))

    def rename(self, node_id, name):
        if not isinstance(name, str) or not name.strip():
            raise GraphError("A node name cannot be empty")
        self.node(node_id)["label"] = name.strip()

    def rename_material(self, name):
        if not isinstance(name, str) or not name.strip():
            raise GraphError("A material name cannot be empty")
        self.data["name"] = name.strip()

    def material_export_name(self):
        return export_identifier(self.data["name"], "Material", "material_")

    def export_names(self):
        """Readable identifiers shared by USD and RDL, without changing graph IDs."""
        bases = {}
        for node in self.data["nodes"]:
            bases[node["id"]] = export_identifier(node["label"], node["shader"], "node_")
        reserved, used, names = set(bases.values()), set(), {}
        for node_id, base in bases.items():
            name = base
            if name in used:
                suffix = 2
                while f"{base}_{suffix}" in used or f"{base}_{suffix}" in reserved:
                    suffix += 1
                name = f"{base}_{suffix}"
            names[node_id] = name
            used.add(name)
        return names

    def set_value(self, node_id, name, value):
        node = self.node(node_id)
        attr = self.catalog.attributes(node["shader"])[name]
        validate_value(attr, value)
        node["values"][name] = copy.deepcopy(value)

    def incoming(self, node_id, port):
        return next((c for c in self.data["connections"] if c["target"] == node_id and c["input"] == port), None)

    def connect(self, source, target, port, output="out"):
        candidate = self.clone()
        attr = self.catalog.attributes(candidate.node(target)["shader"]).get(port, {})
        if attr.get("attrType") not in {"SceneObjectVector", "SceneObjectIndexable"}:
            candidate.disconnect(target, port)
        candidate.data["connections"].append({"source": source, "target": target, "input": port, "output": output})
        node = candidate.node(target)
        if port not in node["ports"]:
            node["ports"].append(port)
        if node["shader"] == "ImageMap" and port in {"gain", "offset_adjust"}:
            node["values"]["gain_offset_enabled"] = True
        candidate.validate()
        self.data = candidate.data

    def disconnect(self, target, port):
        self.data["connections"] = [c for c in self.data["connections"] if (c["target"], c["input"]) != (target, port)]

    def set_terminal(self, terminal, node_id):
        if terminal not in TERMINALS:
            raise GraphError(f"Unknown material output: {terminal}")
        if node_id is not None and self.catalog.category(self.node(node_id)["shader"]) != TERMINALS[terminal]:
            raise GraphError(f"The {terminal} output requires a {TERMINALS[terminal].lower()} shader")
        self.data[terminal] = node_id

    def remove(self, ids):
        self.data["nodes"] = [n for n in self.data["nodes"] if n["id"] not in ids]
        self.data["connections"] = [c for c in self.data["connections"] if c["source"] not in ids and c["target"] not in ids]
        for terminal in TERMINALS:
            if self.data[terminal] in ids:
                self.data[terminal] = None

    def validate(self, require_surface=False):
        try:
            self._validate(require_surface)
        except (KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise GraphError(f"Malformed graph document: {exc}") from exc

    def _validate(self, require_surface):
        d = self.data
        if not isinstance(d, dict) or d.get("version") != 3:
            raise GraphError("Unsupported graph format (expected version 1, 2, or 3)")
        if not isinstance(d.get("material_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", d["material_id"]):
            raise GraphError("Invalid material identity")
        if not isinstance(d["nodes"], list) or not isinstance(d["connections"], list) or not isinstance(d["name"], str):
            raise GraphError("Invalid graph structure")
        if "usd_auto_update" in d and type(d["usd_auto_update"]) is not bool:
            raise GraphError("USD auto update must be a boolean")
        nodes = {}
        for n in d["nodes"]:
            if not isinstance(n["id"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", n["id"]) or n["id"] in nodes or n["id"] == "__output__":
                raise GraphError("Node IDs must be unique USD identifiers")
            nodes[n["id"]] = n
            attrs = self.catalog.attributes(n["shader"])
            if not isinstance(n["label"], str) or not isinstance(n["values"], dict):
                raise GraphError("Invalid node label or values")
            if "preview_srgb" in n and not isinstance(n["preview_srgb"], bool):
                raise GraphError("Node preview_srgb must be a boolean")
            if "tx_textures" in n:
                fields = n["tx_textures"]
                if (not isinstance(fields, list) or any(not isinstance(name, str) for name in fields)
                        or len(fields) != len(set(fields))
                        or any(name not in attrs or not attrs[name].get("filename")
                               or attrs[name].get("attrType") != "String" for name in fields)):
                    raise GraphError("Invalid .tx texture inputs")
            if "usd_auto_update" in n and type(n["usd_auto_update"]) is not bool:
                raise GraphError("Node USD auto update must be a boolean")
            if "usd_camera" in n:
                reference = n["usd_camera"]
                if (n["shader"] not in {"PerspectiveCamera", "OrthographicCamera"} or not isinstance(reference, dict)
                        or not {"scene", "path", "frame"} <= set(reference) <= {"scene", "path", "frame", "follow"}
                        or "follow" in reference and type(reference["follow"]) is not bool
                        or any(not isinstance(reference[k], str) or not reference[k] for k in ("scene", "path"))
                        or not reference["path"].startswith("/")
                        or type(reference["frame"]) not in (int, float) or not math.isfinite(reference["frame"])):
                    raise GraphError("Invalid USD camera reference")
            if "usd_transform" in n:
                reference = n["usd_transform"]
                if (projection_matrix_mode(self.catalog, n["shader"]) is None or not isinstance(reference, dict)
                        or not {"scene", "path", "frame"} <= set(reference) <= {"scene", "path", "frame", "follow"}
                        or "follow" in reference and type(reference["follow"]) is not bool
                        or any(not isinstance(reference[k], str) or not reference[k] for k in ("scene", "path"))
                        or not reference["path"].startswith("/") or reference["path"] == "/"
                        or type(reference["frame"]) not in (int, float) or not math.isfinite(reference["frame"])):
                    raise GraphError("Invalid USD transform reference")
            if "usd_light" in n:
                reference = n["usd_light"]
                if (self.catalog.category(n["shader"]) != "Light" or not isinstance(reference, dict)
                        or set(reference) != {"scene", "path"}
                        or any(not isinstance(value, str) or not value for value in reference.values())
                        or not reference["path"].startswith("/")):
                    raise GraphError("Invalid USD light reference")
            validate_value({"attrType": "Vec2d"}, n["position"])
            if not isinstance(n["ports"], list) or len(n["ports"]) != len(set(n["ports"])):
                raise GraphError("Invalid exposed ports")
            for port in n["ports"]:
                if port not in attrs or not self.catalog.input_kind(attrs[port]):
                    raise GraphError(f"{n['shader']}.{port} does not accept shader connections")
            for name, value in n["values"].items():
                if name not in attrs or not editable(attrs[name]):
                    raise GraphError(f"Unsupported parameter: {n['shader']}.{name}")
                validate_value(attrs[name], value)
        occupied = set()
        adjacency = {node_id: [] for node_id in nodes}
        for c in d["connections"]:
            src, dst, port = c["source"], c["target"], c["input"]
            if src not in nodes or dst not in nodes:
                raise GraphError("Connection references a missing node")
            attr = self.catalog.attributes(nodes[dst]["shader"]).get(port)
            if not attr or not self.catalog.accepts(nodes[src]["shader"], attr):
                raise GraphError(f"Incompatible connection to {nodes[dst]['shader']}.{port}")
            output = c.get("output", "out")
            if output not in self.catalog.outputs(nodes[src]["shader"]):
                raise GraphError("Unknown source output")
            if attr["attrType"].startswith("SceneObject") and output != "out":
                raise GraphError("Scene references require the object's primary output")
            multiple = attr["attrType"] in {"SceneObjectVector", "SceneObjectIndexable"}
            key = (dst, port, src if multiple else None)
            if key in occupied:
                raise GraphError("An input can have only one connection")
            if port not in nodes[dst]["ports"]:
                raise GraphError("Connected inputs must be exposed on the graph")
            if self.catalog.input_kind(attr) == "ImageBuffer" and nodes[src]["shader"] == "RenderOutput":
                source_node = nodes[src]
                if self.value(source_node, "result") in {5, 11, 13} or self.value(source_node, "output_type") == "deep":
                    raise GraphError("Display filters cannot consume deep, time-per-pixel, weight, or cryptomatte outputs")
            occupied.add(key)
            adjacency[src].append(dst)
        # Kahn's algorithm also handles very large user graphs without recursion.
        indegree = {n: 0 for n in nodes}
        for children in adjacency.values():
            for child in children:
                indegree[child] += 1
        ready = [n for n in nodes if indegree[n] == 0]
        visited = 0
        while ready:
            node_id = ready.pop()
            visited += 1
            for child in adjacency[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if visited != len(nodes):
            raise GraphError("Shader connections cannot form a cycle")
        for terminal, category in TERMINALS.items():
            node_id = d[terminal]
            if node_id is not None and (node_id not in nodes or self.catalog.category(nodes[node_id]["shader"]) != category):
                raise GraphError(f"The {terminal} output must be a {category.lower()} node")
        if require_surface and not any(d[t] for t in ("surface", "volume", "light", "display", "aov", "geometry", "camera", "lightset")):
            raise GraphError("Choose a surface output, volume, light, display filter, or render output before rendering or exporting")
        preview = d["preview"]
        if not isinstance(preview, dict) or set(preview) != set(PREVIEW_DEFAULTS):
            raise GraphError("Invalid preview settings")
        if type(preview["displacement_enabled"]) is not bool:
            raise GraphError("Preview displacement enabled must be a boolean")
        if preview["subdivision_scheme"] not in ("none", "catmullClark", "bilinear"):
            raise GraphError("Unsupported preview subdivision scheme")
        resolution = preview["mesh_resolution"]
        if type(resolution) is not int or not 1 <= resolution <= TESSELLATION_MAX:
            raise GraphError(f"Preview mesh resolution must be an integer from 1 to {TESSELLATION_MAX}")
        if type(preview["shadow_terminator_fix"]) is not int or not 0 <= preview["shadow_terminator_fix"] <= 4:
            raise GraphError("Unsupported shadow terminator correction")
        error = preview["adaptive_error"]
        validate_value({"attrType": "Float"}, error)
        if not 0 <= error <= 64:
            raise GraphError("Preview adaptive error must be from 0 to 64 pixels")
        if preview["geometry"] not in GEOMETRIES or preview["render_view"] not in RENDER_VIEWS:
            raise GraphError("Unsupported preview geometry or render view")
        validate_value({"attrType": "Float"}, preview["studio_light_level"])
        if not 0 <= preview["studio_light_level"] <= 100:
            raise GraphError("Studio light level must be from 0 to 100")
        if type(preview["light_sampling_mode"]) is not int or preview["light_sampling_mode"] not in (0, 1):
            raise GraphError("light_sampling_mode must be Uniform (0) or Adaptive (1)")
        quality = preview["light_sampling_quality"]
        if type(quality) not in (int, float) or not math.isfinite(quality) or not 0 <= quality <= 1:
            raise GraphError("light_sampling_quality must be a finite number from 0 to 1")
        for name in ("samples", *LOBE_SAMPLES, *LIGHT_SAMPLES, *RAY_DEPTHS):
            minimum = 0 if name in RAY_DEPTHS else 1
            maximum = 32 if name == "samples" else 64
            if type(preview[name]) is not int or not minimum <= preview[name] <= maximum:
                raise GraphError(f"{name} must be an integer from {minimum} to {maximum}")
        validate_value({"attrType": "Double"}, preview["vdb_scale"])
        if preview["vdb_scale"] <= 0:
            raise GraphError("VDB scale must be positive")
        validate_value({"attrType": "Vec3d"}, preview["vdb_offset"])
        for name in ("vdb_file", "vdb_grid"):
            if not isinstance(preview[name], str):
                raise GraphError(f"{name} must be text")

    def save(self, path):
        self.validate()
        path = Path(path)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=f".{path.name}.", delete=False) as f:
            temp = Path(f.name)
            try:
                json.dump(self.data, f, indent=2, allow_nan=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                temp.unlink(missing_ok=True)
                raise
        try:
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    @classmethod
    def load(cls, catalog, path):
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            raise GraphError("A graph document must be a JSON object")
        graph = cls(catalog, data)
        base = Path(path).resolve().parent
        for node in graph.data["nodes"]:
            for name, attr in catalog.attributes(node["shader"]).items():
                val = node["values"].get(name)
                if attr.get("filename") and isinstance(val, str) and val and not Path(val).is_absolute():
                    node["values"][name] = str(base / val)
        val = graph.data["preview"]["vdb_file"]
        if val and not Path(val).is_absolute():
            graph.data["preview"]["vdb_file"] = str(base / val)
        return graph


def starter_graph(catalog):
    graph = Graph(catalog)
    graph.data["name"] = "Porcelain / checker"
    checker = graph.add("CheckerboardMap", (-370, 25))
    checker["label"] = "Porcelain checker"
    checker["values"] = {"color_A": [0.06, 0.32, 0.29], "color_B": [0.76, 0.79, 0.67], "num_u_tiles": 12, "num_v_tiles": 8}
    surface = graph.add("DwaBaseMaterial", (0, 0))
    surface["label"] = "Glazed porcelain"
    surface["values"] = {"roughness": 0.23, "show_clearcoat": True, "clearcoat_roughness": 0.12}
    graph.connect(checker["id"], surface["id"], "albedo")
    return graph
