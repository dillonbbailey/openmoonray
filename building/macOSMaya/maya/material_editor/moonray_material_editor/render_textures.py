"""Prepare native-render texture copies without editing source USD or images."""
from pathlib import Path

from .map_preview import IMAGE_SUFFIXES, prepare_texture_paths
from .native import quote
from .render_scene_objects import read_scene


def prepare_scene_textures(scene, directory):
    context = read_scene(scene)
    fields = []
    for name, interfaces in context.getSceneObjectNamesAndTypes().items():
        if not {"MAP", "LIGHT"}.intersection(interfaces.split(" | ")):
            continue
        obj = context.getSceneObject(name)
        cls = obj.getSceneClass()
        for key in cls.getAttributeNames():
            attr = cls.getAttribute(key)
            if attr.isFilename() and attr.getTypeName() == "String":
                source = obj.get(key)
                if source and Path(source).suffix.lower() in IMAGE_SUFFIXES:
                    fields.append((cls.getName(), name, key, source))
    if not fields:
        return
    textures = Path(directory).resolve() / "textures"
    textures.mkdir(exist_ok=True)
    converted = prepare_texture_paths([source for _, _, _, source in fields], textures)
    with Path(scene).open("a") as stream:
        for cls, name, key, source in fields:
            if converted[source] != source:
                print(f"Prepared render texture: {source} → {converted[source]}", flush=True)
                stream.write(f"\n{cls}({quote(name)}) {{ [{quote(key)}] = {quote(converted[source])} }}\n")
