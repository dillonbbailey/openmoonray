"""Bake color maps, with optional motion blur, in the isolated renderer."""
from pathlib import Path
import tempfile

def bake_texture(data, directory):
    from .map_preview import bake_map
    from .model import Catalog, Graph
    from .worker import run_native_renderer
    graph = Graph(Catalog(), data["graph"])
    shader = graph.node(data["node"])["shader"]
    motion = data.get("motion_blur") is not None
    if graph.catalog.category(shader) != "Map":
        raise ValueError("Choose a color map to bake.")
    size = data["size"]
    if type(size) is not int or not 16 <= size <= 8192:
        raise ValueError("Bake resolution must be between 16 and 8192 pixels.")
    target = Path(data["destination"]).expanduser().absolute()
    if target.suffix.lower() != ".exr" or not target.parent.is_dir() or target.is_dir():
        raise ValueError("Choose an .exr filename in an existing folder.")
    # A bake creates a new asset; never replace an input still used by the graph.
    from .map_preview import asset_files
    for node in graph.data["nodes"]:
        for name, attr in graph.catalog.attributes(node["shader"]).items():
            source = graph.value(node, name)
            if attr.get("filename") and isinstance(source, str) and source:
                if any(path.resolve() == target.resolve() for path in asset_files(source)):
                    raise ValueError("Choose a destination different from the source texture.")
    if motion:
        from .motion_bake import bake_motion_map
        output = bake_motion_map(data, directory, size)
    else:
        output = bake_map(data, directory, size=size, progress_step=1)
    print("Preparing mipmapped texture…", flush=True)
    # Stage beside the destination so replacement is atomic on any filesystem.
    with tempfile.TemporaryDirectory(prefix=".lunatic-bake-", dir=target.parent) as scratch:
        staged = Path(scratch) / "texture.exr"
        run_native_renderer(["maketx", "--oiio", "--threads", "2", "--format", "openexr",
                             str(output), "-o", str(staged)])
        if not staged.is_file():
            raise ValueError("The bake did not produce a texture.")
        staged.replace(target)
    if motion:
        print("Render progress: 100%", flush=True)
    return dict(path=str(target), width=size, height=size)
