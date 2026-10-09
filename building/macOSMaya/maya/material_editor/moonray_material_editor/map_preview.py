"""Isolated, renderer-evaluated map swatches and their cacheable inputs."""
import copy
import glob
import hashlib
import json
import os
from pathlib import Path

from .model import EDITOR_ROOT, Graph

IMAGE_SUFFIXES = {".exr", ".tx", ".tex", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".hdr",
                  ".bmp", ".tga", ".webp", ".psd", ".dds", ".pic", ".sgi", ".iff", ".ppm", ".pfm", ".rat"}
UDIM_TOKENS = ("<UDIM>", "<udim>", "%(UDIM)d")


def absolute_asset(value, base):
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else Path(base) / path).resolve()


def asset_files(path):
    """Return the actual files behind an ordinary filename or UDIM pattern."""
    pattern = glob.escape(str(path))
    for token in UDIM_TOKENS:
        if token in str(path):
            pattern = pattern.replace(glob.escape(token), "[1-9][0-9][0-9][0-9]")
            return [Path(p) for p in sorted(glob.glob(pattern))]
    return [Path(path)]


def texture_path(value, base):
    path = absolute_asset(value, base)
    if any(token in str(path) for token in UDIM_TOKENS):
        files = asset_files(path)
        if files:
            return files[0], "First UDIM tile · "
    return path, ""


def map_snapshot(graph, node_id, base=EDITOR_ROOT):
    """Copy only upstream inputs, ignoring layout, names and preview settings."""
    if graph.catalog.category(graph.node(node_id)["shader"]) not in {"Map", "NormalMap"}:
        raise ValueError("Map previews require a map or normal map.")
    needed, pending = set(), [node_id]
    incoming = {}
    for connection in graph.data["connections"]:
        incoming.setdefault(connection["target"], []).append(connection)
    while pending:
        current = pending.pop()
        if current not in needed:
            needed.add(current)
            pending.extend(c["source"] for c in incoming.get(current, []))
    preview = Graph(graph.catalog)
    # Transient swatches have no material association. Keep their identity
    # deterministic so UI-only changes reuse the existing rendered thumbnail.
    preview.data["material_id"] = "0" * 32
    preview.data["name"] = "Map preview"
    stamps = []
    for node in sorted(graph.data["nodes"], key=lambda n: n["id"]):
        if node["id"] not in needed:
            continue
        node = copy.deepcopy(node)
        node.pop("preview_srgb", None)  # Display-only state never changes map evaluation.
        node.update(position=[0, 0], label=node["id"],
                    ports=sorted({c["input"] for c in incoming.get(node["id"], [])}))
        for name, attr in graph.catalog.attributes(node["shader"]).items():
            source = graph.value(node, name)
            if attr.get("filename") and isinstance(source, str) and source and "://" not in source:
                path = absolute_asset(source, base)
                node["values"][name] = str(path)
                for asset in asset_files(path):
                    try:
                        stat = asset.stat()
                        stamp = (stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        stamp = None
                    stamps.append((str(asset), stamp))
        preview.data["nodes"].append(node)
    preview.data["connections"] = sorted(
        [copy.deepcopy(c) for c in graph.data["connections"] if c["target"] in needed],
        # Stable sorting preserves authored order within array inputs.
        key=lambda c: (c["target"], c["input"]))
    payload = dict(graph=preview.data, node=node_id)
    key = hashlib.sha256(json.dumps([payload, stamps], sort_keys=True).encode()).hexdigest()
    return key, payload


def preview_graph(catalog, data, node_id, output_channel="out"):
    graph = Graph(catalog, data)
    normal = catalog.category(graph.node(node_id)["shader"]) == "NormalMap"
    graph.data["preview"].update(geometry="card", subdivision_scheme="none", samples=2,
                                  displacement_enabled=False, max_depth=1)
    output = node_id
    if normal:
        convert = graph.add("NormalToRgbMap")["id"]
        graph.connect(output, convert, "input")
        remap = graph.add("OpMap")
        # Encode signed normals before emission: negative radiance is clamped.
        remap["values"].update(operation=0, op1_factor=.5, op2=[.5, .5, .5])
        graph.connect(convert, remap["id"], "op1")
        output = remap["id"]
    material = graph.add("DwaEmissiveMaterial")["id"]
    graph.connect(output, material, "emission", "out" if normal else output_channel)
    graph.set_terminal("surface", material)
    camera = graph.add("OrthographicCamera")
    camera["values"].update(film_width_aperture=2., near=.1,
                             node_xform=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 3, 1]])
    graph.set_terminal("camera", camera["id"])
    return graph, normal


def prepare_texture_paths(sources, directory):
    """Prepare tiled, mipmapped copies, preserving source files and color values."""
    import OpenImageIO as oiio
    from .worker import run_native_renderer
    converted = {}
    for source in dict.fromkeys(sources):
        files = asset_files(source)
        if not files or any(not path.is_file() for path in files):
            raise ValueError("Texture not found: " + source)
        token = next((t for t in UDIM_TOKENS if t in source), None)
        target = directory / (f"texture_{len(converted)}" + ("." + token if token else "") + ".tx")
        needs_conversion = False
        for path in files:
            image = oiio.ImageInput.open(str(path))
            if not image:
                raise ValueError("Could not read texture: " + str(path))
            try:
                tiled = image.spec().tile_width > 0
                single_pixel = max(image.spec().width, image.spec().height) == 1
                mipmapped = single_pixel or image.seek_subimage(0, 1)
            finally:
                image.close()
            needs_conversion |= not (tiled and mipmapped)
        if needs_conversion:
            for path in files:
                destination = str(target)
                if token:
                    offset = source.index(token)
                    destination = destination.replace(token, str(path)[offset:offset + 4])
                run_native_renderer(["maketx", "--oiio", "--threads", "2", str(path), "-o", destination])
            converted[source] = str(target)
        else:
            converted[source] = source
    return converted


def prepare_textures(graph, directory):
    """Make temporary mipmaps for thumbnails without changing source assets."""
    fields = [(node, name, node["values"].get(name))
              for node in graph.data["nodes"]
              for name, attr in graph.catalog.attributes(node["shader"]).items()
              if attr.get("filename") and node["values"].get(name)
              and Path(node["values"][name]).suffix.lower() in IMAGE_SUFFIXES]
    converted = prepare_texture_paths([source for _, _, source in fields], directory)
    for node, name, source in fields:
        node["values"][name] = converted[source]


def bake_map(data, directory, size=128, progress_step=None):
    from .model import Catalog
    from .native import create_scene
    from .worker import run_native_renderer
    if type(size) is not int or not 16 <= size <= 8192:
        raise ValueError("Map texture size must be between 16 and 8192 pixels.")
    graph, _ = preview_graph(Catalog(), data["graph"], data["node"], data.get("output", "out"))
    from .tx_cache import prepared_graph
    graph = prepared_graph(graph)
    prepare_textures(graph, directory)
    scene = directory / "map.rdla"
    output = create_scene(graph, scene, directory, size=size)
    # Pad the card outside the camera's 0–1 UV window. Filter samples beyond
    # the image edge must still hit the map instead of creating a black rim.
    with scene.open("a") as file:
        file.write('''\npreview_object {
    ["vertex_list_0"] = {Vec3(-1.05, -1.05, 0), Vec3(1.05, -1.05, 0),
                         Vec3(-1.05, 1.05, 0), Vec3(1.05, 1.05, 0)},
    ["uv_list"] = {Vec2(-0.025, -0.025), Vec2(1.025, -0.025),
                   Vec2(1.025, 1.025), Vec2(-0.025, 1.025)}
}\n''')
    run_native_renderer(["moonray", "-in", str(scene), "-exec_mode", "scalar", "-threads", "2"], progress_step=progress_step)
    return output


def render_map(data, directory):
    from .model import Catalog
    from .texture_thumbnail import make_thumbnail
    output = bake_map(data, directory)
    catalog = Catalog()
    normal = catalog.category(Graph(catalog, data["graph"]).node(data["node"])["shader"]) == "NormalMap"
    srgb = make_thumbnail(str(output), directory / "thumbnail-srgb.png")
    raw = make_thumbnail(str(output), directory / "thumbnail-raw.png", raw=True)
    return dict(raw if normal else srgb, images={"srgb": srgb["image"], "raw": raw["image"]})
