"""Persistent, atomic texture conversions shared by UI and render workers."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from .map_preview import IMAGE_SUFFIXES, UDIM_TOKENS, absolute_asset, asset_files
from .model import EDITOR_ROOT
from .texture_preferences import cache_folder


def texture_attribute(shader, name, attr, value):
    if not attr.get("filename") or attr.get("attrType") != "String":
        return False
    if shader in {"OpenVdbMap", "OpenVdbMap_v2", "VdbGeometry", "VdbLightFilter",
                  "UsdGeometry", "UsdInstanceGeometry", "RenderOutput"}:
        return False
    return not value or Path(value).suffix.lower() in IMAGE_SUFFIXES


def plan_texture(source, base=EDITOR_ROOT, cache=None):
    if not source:
        raise ValueError("Choose a texture file first.")
    if "://" in source:
        raise ValueError("Use .tx requires a local texture file.")
    source = str(absolute_asset(source, base))
    if Path(source).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Use .tx requires an image texture.")
    files = asset_files(source)
    if not files or any(not path.is_file() for path in files):
        raise ValueError("Texture not found: " + source)
    stamps = [(str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files]
    key = hashlib.sha256(json.dumps([1, source, stamps]).encode()).hexdigest()
    directory = Path(cache or cache_folder()).resolve() / key
    token = next((token for token in UDIM_TOKENS if token in source), None)
    target = directory / ("texture." + token + ".tx" if token else "texture.tx")
    outputs = [str(target).replace(token, str(path)[source.index(token):source.index(token) + 4])
               if token else str(target) for path in files]
    return dict(key=key, source=source, files=[str(p) for p in files], output=str(target),
                outputs=outputs, directory=str(directory))


def cache_ready(plan):
    try:
        recorded = json.loads((Path(plan["directory"]) / "complete.json").read_text())
        return (recorded["key"] == plan["key"] and recorded["outputs"] == plan["outputs"]
                and recorded["sizes"] == [Path(path).stat().st_size for path in plan["outputs"]]
                and all(size > 0 for size in recorded["sizes"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prepare_texture(source, base=EDITOR_ROOT, cache=None):
    """Runs only in workers. No source or live graph is modified."""
    plan = plan_texture(source, base, cache)
    directory = Path(plan["directory"])
    directory.parent.mkdir(parents=True, exist_ok=True)
    # Renders and the UI may request the same conversion simultaneously.
    with directory.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cache_ready(plan):
            return plan["output"]
        import OpenImageIO as oiio
        directory.mkdir(exist_ok=True)
        (directory / "complete.json").unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix=".building-", dir=directory) as scratch:
            built = []
            for index, (source_file, output) in enumerate(zip(plan["files"], plan["outputs"])):
                target = Path(scratch) / Path(output).name
                print(f"Preparing .tx {index + 1}/{len(plan['files'])}: {source_file}", flush=True)
                result = subprocess.run(["maketx", "--oiio", "--threads", "2", source_file, "-o", str(target)],
                                        capture_output=True, text=True, timeout=300)
                if result.returncode:
                    raise ValueError("Texture conversion failed: " + (result.stderr or result.stdout)[-2000:])
                image = oiio.ImageInput.open(str(target))
                if not image:
                    raise ValueError("Cannot read converted texture: " + oiio.geterror())
                try:
                    spec = image.spec()
                    if not spec.tile_width or (max(spec.width, spec.height) > 1 and not image.seek_subimage(0, 1)):
                        raise ValueError("Converted texture is not tiled and mipmapped.")
                finally:
                    image.close()
                built.append((target, Path(output)))
            if plan_texture(plan["source"], cache=directory.parent)["key"] != plan["key"]:
                raise ValueError("Source texture changed during conversion. Retry using the updated source.")
            for temporary, target in built:
                temporary.replace(target)
            marker = Path(scratch) / "complete.json"
            marker.write_text(json.dumps(dict(key=plan["key"], outputs=plan["outputs"],
                                             sizes=[Path(p).stat().st_size for p in plan["outputs"]])))
            marker.replace(directory / "complete.json")
    return plan["output"]


def prepared_graph(graph, base=EDITOR_ROOT):
    """Resolve opted-in inputs on a render/export copy, retaining source graphs."""
    fields = [(node["id"], name, graph.value(node, name)) for node in graph.data["nodes"]
              for name in node.get("tx_textures", [])]
    if not fields:
        return graph
    result = graph.clone()
    for node_id, name, source in fields:
        if source:
            result.node(node_id)["values"][name] = prepare_texture(source, base)
    # A prepared copy must not convert its own cached paths a second time.
    for node in result.data["nodes"]:
        node.pop("tx_textures", None)
    return result
