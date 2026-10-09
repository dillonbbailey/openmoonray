"""Read USD shader networks and apply graph edits without replacing bindings."""
import copy
import hashlib
import json
from pathlib import Path

from pxr import Sdf, Usd, UsdShade

from .model import Graph, SHADER_TYPES, editable, PROJECTION_CAMERAS, GLITTER_SPACE_FIELDS
from .usd_editing import decode_value, editable_prim, encode_value
from .usd_materials import material_catalog, unique_path
from .worker import create_stage

TERMINALS = ("surface", "displacement", "volume")


def resolve_source(attr):
    """Follow material/nodegraph interfaces, including forwarded constants."""
    seen = set()
    while attr:
        path = str(attr.GetPath())
        if path in seen:
            raise ValueError("Cyclic USD shader interface: " + path)
        seen.add(path)
        connections = attr.GetConnections()
        if not connections:
            return attr
        if len(connections) != 1:
            raise ValueError("Multiple sources on one shader input are not supported: " + path)
        attr = attr.GetStage().GetAttributeAtPath(connections[0])
        if not attr:
            raise ValueError("Missing shader connection: " + str(connections[0]))
    return attr


def material_outputs(prim):
    material = UsdShade.Material(prim)
    result = {}
    if material:
        for terminal in TERMINALS:
            for name in ("moonray:" + terminal, terminal):
                output = material.GetOutput(name)
                if output and output.GetAttr().GetConnections():
                    result[terminal] = output.GetAttr()
                    break
    return result


def upstream_paths(attrs):
    found, pending = set(), list(attrs)
    while pending:
        attr = resolve_source(pending.pop())
        if not attr or not attr.GetName().startswith("outputs:") or not attr.GetPrim().IsA(UsdShade.Shader):
            continue
        path = str(attr.GetPrimPath())
        if path not in found:
            found.add(path)
            pending.extend(i.GetAttr() for i in UsdShade.Shader(attr.GetPrim()).GetInputs() if i.GetAttr().GetConnections())
    return found


def target_prim(stage, path):
    prim = stage.GetPrimAtPath(path)
    if not editable_prim(prim):
        raise ValueError("Select an editable USD shader or material.")
    if prim.IsA(UsdShade.Shader):
        # Copied preview scenes keep shader prims outside the Material prim.
        candidates = []
        for candidate in stage.Traverse():
            if candidate.IsA(UsdShade.Material):
                try:
                    if path in upstream_paths(material_outputs(candidate).values()):
                        candidates.append(candidate)
                except ValueError:
                    continue
        parent = next((p for p in candidates if prim.GetPath().HasPrefix(p.GetPath())), None)
        return parent or (candidates[0] if len(candidates) == 1 else prim)
    if prim.IsA(UsdShade.Material):
        return prim
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(UsdShade.Tokens.full)
    if not material:
        raise ValueError("This prim has no bound material. Select a Shader or Material in the scene tree.")
    return material.GetPrim()


def fingerprint(stage, link):
    paths = set(link["paths"].values()) | {link["path"]}
    records = []
    for path in sorted(paths):
        prim = stage.GetPrimAtPath(path)
        if not editable_prim(prim):
            raise ValueError("A linked shader was removed or is no longer editable. Reopen its material.")
        attrs = []
        for attr in sorted(prim.GetAttributes(), key=lambda a: a.GetName()):
            attrs.append((attr.GetName(), str(attr.GetTypeName()), encode_value(attr.Get()),
                          [(t, encode_value(attr.Get(t))) for t in attr.GetTimeSamples()],
                          list(map(str, attr.GetConnections()))))
        records.append((path, prim.GetTypeName(), prim.GetDisplayName(), attrs))
    # Interface inputs may live on nodegraphs or other materials.
    for path in sorted(link.get("interfaces", [])):
        attr = stage.GetAttributeAtPath(path)
        records.append((path, encode_value(attr.Get()) if attr else None,
                        [(t, encode_value(attr.Get(t))) for t in attr.GetTimeSamples()] if attr else [],
                        list(map(str, attr.GetConnections())) if attr else []))
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def linked_graph(stage, path, scene, frame=0):
    target = target_prim(stage, path)
    catalog = material_catalog()
    # Reopening a saved editor material retains its graph/node identities, so
    # other bindings of that graph continue to synchronize with the same tab.
    from .usd_material_sync import KEY
    stored = target.GetCustomDataByKey(KEY)
    if stored:
        try:
            record = json.loads(stored)
            link = record["link"]
            graph = Graph(catalog, link["baseline"])
            animated = any(attr.GetNumTimeSamples() for shader_path in link["paths"].values()
                           for attr in stage.GetPrimAtPath(shader_path).GetAttributes())
            if (link["path"] == str(target.GetPath()) and graph.data["material_id"] == record["material_id"]
                    and (not animated or link["frame"] == frame)
                    and fingerprint(stage, link) == link["fingerprint"]):
                nodes = {n["id"] for n in graph.data["nodes"]}
                selected = next((key for key, value in link["paths"].items() if value == path and key in nodes),
                                next((graph.data[t] for t in TERMINALS if graph.data[t]), None))
                if selected:
                    link.update(scene=scene, frame=frame)
                    return dict(graph=graph.data, link=link, selected=selected)
        except (ValueError, KeyError, TypeError, RuntimeError):
            pass  # Changed/external networks are imported from their actual USD inputs.
    graph = Graph(catalog)
    graph.rename_material(target.GetDisplayName() or target.GetName())
    paths, visited, visiting, interfaces = {}, {}, set(), set()
    time = Usd.TimeCode(frame)

    def source(attr):
        current = attr
        seen = set()
        while current and current.GetConnections():
            if str(current.GetPath()) in seen:
                break  # resolve_source supplies the error.
            seen.add(str(current.GetPath()))
            interfaces.add(str(current.GetPath()))
            current = stage.GetAttributeAtPath(current.GetConnections()[0])
        if current:
            interfaces.add(str(current.GetPath()))
        return resolve_source(attr)

    def visit(prim, depth=0):
        shader_path = str(prim.GetPath())
        if shader_path in visiting:
            raise ValueError("Shader connections cannot form a cycle: " + shader_path)
        if shader_path in visited:
            return visited[shader_path]
        if not editable_prim(prim) or not prim.IsA(UsdShade.Shader):
            raise ValueError("Unsupported shader source: " + shader_path)
        shader = UsdShade.Shader(prim)
        kind = shader.GetIdAttr().Get(time)
        if kind not in catalog.shaders or (catalog.definition(kind)["type"] not in SHADER_TYPES and kind not in PROJECTION_CAMERAS):
            raise ValueError(f"{shader_path} uses unsupported shader {kind!r}. The Material Editor needs a MoonRay or supported USD shader ID.")
        visiting.add(shader_path)
        node = graph.add(kind, (-320 * depth, len(visited) * 180))
        # Import the source's native defaults when no USD opinion is authored.
        # Object-space defaults apply only to newly created materials.
        for name in GLITTER_SPACE_FIELDS:
            node["values"].pop(name, None)
        node_id = node["id"]
        node["label"] = prim.GetDisplayName() or prim.GetName()
        reference = prim.GetCustomDataByKey("editor:usdCamera")
        if reference and kind in PROJECTION_CAMERAS:
            node["usd_camera"] = dict(reference)
        reference = prim.GetCustomDataByKey("editor:usdTransform")
        if reference:
            node["usd_transform"] = dict(reference)
        position = prim.GetCustomDataByKey("editor:position")
        if position is not None:
            node["position"] = list(position)
        visited[shader_path], paths[node_id] = node_id, shader_path
        attrs = catalog.attributes(kind)
        for port in shader.GetInputs():
            name, attr = port.GetBaseName(), port.GetAttr()
            if not attr.HasAuthoredValueOpinion() and not attr.GetConnections():
                continue
            if name not in attrs:
                raise ValueError(f"Unsupported input {shader_path}.inputs:{name}; the network was not imported.")
            resolved = source(attr)
            value_attr = resolved if resolved and resolved.GetName().startswith("inputs:") else attr
            value = value_attr.Get(time)
            if value is not None and editable(attrs[name]):
                if attrs[name].get("enum") and isinstance(value, str):
                    if value not in attrs[name]["enum"]:
                        raise ValueError(f"Unknown enum value {value!r} on {attr.GetPath()}")
                    value = attrs[name]["enum"][value]
                if isinstance(value, Sdf.AssetPath) or attrs[name].get("filename"):
                    value = (value.resolvedPath or value.path) if isinstance(value, Sdf.AssetPath) else value
                    if value and not Path(value).is_absolute() and "://" not in value:
                        stack = value_attr.GetPropertyStack(time)
                        if stack:
                            layer = stack[0].layer
                            value = Sdf.ComputeAssetPathRelativeToLayer(layer, value)
                            if not Path(value).is_absolute() and layer.realPath:
                                value = str(Path(layer.realPath).parent / value)
                graph.set_value(node_id, name, encode_value(value))
            if resolved and resolved.GetName().startswith("outputs:"):
                upstream = visit(resolved.GetPrim(), depth + 1)
                output = resolved.GetBaseName()
                channel = "out" if output in ("surface", "out", "rgb", "displacement", "volume") else output
                graph.connect(upstream, node_id, name, channel)
        visiting.remove(shader_path)
        return node_id

    outputs = material_outputs(target)
    if target.IsA(UsdShade.Shader):
        selected = visit(target)
        category = catalog.category(graph.node(selected)["shader"])
        for terminal in TERMINALS:
            if category == terminal.capitalize() or terminal == "surface" and category == "Material":
                graph.set_terminal(terminal, selected)
    else:
        if not outputs:
            raise ValueError("This material has no connected MoonRay or universal surface, volume, or displacement output.")
        for terminal, attr in outputs.items():
            resolved = source(attr)
            graph.set_terminal(terminal, visit(resolved.GetPrim()))
        selected = visited.get(path) or graph.data["surface"] or graph.data["volume"] or graph.data["displacement"]
    graph.validate()
    link = dict(scene=scene, path=str(target.GetPath()), frame=frame, paths=paths,
                outputs={key: attr.GetName().removeprefix("outputs:") for key, attr in outputs.items()},
                interfaces=sorted(interfaces), baseline=copy.deepcopy(graph.data))
    link["fingerprint"] = fingerprint(stage, link)
    return dict(graph=graph.data, link=link, selected=selected)


def exported_network(data):
    graph = Graph(material_catalog(), data)
    # A standalone map/displacement still needs the exporter's material shell.
    dummy = None
    if not graph.data["surface"] and not graph.data["volume"]:
        dummy = graph.add("DwaBaseMaterial")["id"]
    stage = create_stage(graph, None)
    shaders, keys = {}, {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        key = prim.GetCustomDataByKey("editor:nodeId") or "adapter:" + prim.GetName()
        if key != dummy:
            shaders[key] = UsdShade.Shader(prim)
            keys[str(prim.GetPath())] = key
    return stage, shaders, keys


def apply_graph(stage, data):
    link = copy.deepcopy(data["link"])
    if fingerprint(stage, link) != link["fingerprint"]:
        raise ValueError("This USD shader network changed after it was opened. Close its Material Editor tab and reopen it before applying.")
    graph = Graph(material_catalog(), data["graph"])
    before_stage, before, before_keys = exported_network(link["baseline"])
    after_stage, after, after_keys = exported_network(graph.data)
    target = stage.GetPrimAtPath(link["path"])
    if target.IsA(UsdShade.Shader):
        root = next(key for key, value in link["paths"].items() if value == link["path"])
        if (root not in after or after[root].GetIdAttr().Get() != target.GetAttribute("info:id").Get()
                or any(graph.data[t] != link["baseline"][t] for t in TERMINALS)):
            raise ValueError("Open a Material prim to replace its terminal shader. A standalone shader can edit its parameters and input network.")
    paths, old_paths = {}, link["paths"]
    parent = target.GetPath() if target.IsA(UsdShade.Material) else target.GetParent().GetPath()
    for key, shader in after.items():
        existing = stage.GetPrimAtPath(old_paths[key]) if key in old_paths else None
        if existing and UsdShade.Shader(existing).GetIdAttr().Get() == shader.GetIdAttr().Get():
            paths[key] = old_paths[key]
        else:
            path = unique_path(stage, parent, shader.GetPrim().GetName())
            created = UsdShade.Shader.Define(stage, path)
            created.CreateIdAttr(shader.GetIdAttr().Get())
            paths[key] = str(path)

    def connections(attr, keys, destinations):
        return [Sdf.Path(destinations[keys[str(p.GetPrimPath())]]).AppendProperty(p.name)
                for p in attr.GetConnections()] if attr else []

    def set_connections(attr, targets):
        for path in targets:
            if not stage.GetAttributeAtPath(path):
                key = next(k for k, v in paths.items() if v == str(path.GetPrimPath()))
                output = after[key].GetPrim().GetAttribute(path.name)
                stage.GetPrimAtPath(path.GetPrimPath()).CreateAttribute(path.name, output.GetTypeName(), custom=False)
        if not attr.SetConnections(targets):
            raise ValueError("Could not connect " + str(attr.GetPath()))

    for key, shader in after.items():
        dest = UsdShade.Shader(stage.GetPrimAtPath(paths[key]))
        previous = before.get(key) if paths[key] == old_paths.get(key) else None
        for name in ("editor:usdCamera", "editor:usdTransform"):
            reference = shader.GetPrim().GetCustomDataByKey(name)
            if reference != dest.GetPrim().GetCustomDataByKey(name):
                if reference:
                    dest.GetPrim().SetCustomDataByKey(name, reference)
                else:
                    dest.GetPrim().ClearCustomDataByKey(name)
        if not previous or shader.GetPrim().GetDisplayName() != previous.GetPrim().GetDisplayName():
            dest.GetPrim().SetDisplayName(shader.GetPrim().GetDisplayName())
        for output in shader.GetOutputs() if not previous else []:
            if not dest.GetOutput(output.GetBaseName()):
                dest.CreateOutput(output.GetBaseName(), output.GetTypeName())
        inputs = {i.GetBaseName() for i in shader.GetInputs()}
        if previous:
            inputs.update(i.GetBaseName() for i in previous.GetInputs())
        attrs = graph.catalog.attributes(shader.GetIdAttr().Get())
        for name in sorted(inputs):
            new = shader.GetInput(name).GetAttr()
            old = previous.GetInput(name).GetAttr() if previous else None
            new_connections = connections(new, after_keys, paths)
            old_connections = connections(old, before_keys, old_paths)
            value_changed = (new.Get() if new else None) != (old.Get() if old else None)
            connected_changed = new_connections != old_connections
            if not value_changed and not connected_changed:
                continue
            port = dest.GetInput(name) or dest.CreateInput(name, (new or old).GetTypeName())
            attr = port.GetAttr()
            if value_changed:
                value = new.Get() if new else attrs[name].get("default")
                if value is not None:
                    value = encode_value(value)
                    if attrs[name].get("filename") and value and "://" not in value and not Path(value).is_absolute():
                        value = str(Path(data["base_dir"]) / value)
                    if attrs[name].get("enum"):
                        if attr.GetTypeName() in (Sdf.ValueTypeNames.Token, Sdf.ValueTypeNames.String):
                            if not isinstance(value, str):
                                value = next(k for k, v in attrs[name]["enum"].items() if v == value)
                        elif isinstance(value, str):
                            value = attrs[name]["enum"][value]
                    converted = decode_value(attr.GetTypeName(), value)
                    samples = [(t, attr.Get(t)) for t in attr.GetTimeSamples()]
                    for time, sample in samples:
                        attr.Set(sample, time)
                    attr.Set(converted, Usd.TimeCode(link["frame"]) if samples else Usd.TimeCode.Default())
                else:
                    attr.Block()
            if connected_changed or value_changed and attr.GetConnections() and not new_connections:
                set_connections(attr, new_connections)

    if target.IsA(UsdShade.Material):
        material = UsdShade.Material(target)
        exported = UsdShade.Material(after_stage.GetDefaultPrim())
        original = UsdShade.Material(before_stage.GetDefaultPrim())
        for terminal in TERMINALS:
            new = exported.GetOutput("moonray:" + terminal).GetAttr() if graph.data[terminal] else None
            old = original.GetOutput("moonray:" + terminal).GetAttr() if link["baseline"][terminal] else None
            new_connections = connections(new, after_keys, paths)
            if new_connections != connections(old, before_keys, old_paths):
                name = link["outputs"].setdefault(terminal, "moonray:" + terminal)
                output = material.GetOutput(name) or material.CreateOutput(name, (new or old).GetTypeName())
                set_connections(output.GetAttr(), new_connections)
    link.update(paths=paths, baseline=copy.deepcopy(graph.data))
    link["fingerprint"] = fingerprint(stage, link)
    return link
