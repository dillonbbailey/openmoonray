"""Texture preferences shared by the Qt host and native Python workers."""
import json
import os
from pathlib import Path
import tempfile


def default_cache_folder():
    # Maya: inside the workspace (model.CACHE_ROOT), not the home directory.
    from .model import CACHE_ROOT
    return str(CACHE_ROOT / "textures")


def config_file():
    from .model import CACHE_ROOT
    return Path(os.environ.get("MOONLAB_TEXTURE_PREFERENCES", str(CACHE_ROOT / "texture-cache.json")))


def cache_folder(config=None):
    try:
        value = json.loads(Path(config or config_file()).read_text())["folder"]
        if isinstance(value, str) and Path(value).is_absolute():
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return default_cache_folder()


def save_cache_folder(value, config=None):
    path = Path(os.path.expandvars(value.strip())).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ValueError("Choose an absolute texture-cache folder (or a path beginning with ~).")
    path.mkdir(parents=True, exist_ok=True)
    # Verify access now, rather than failing later during rendering.
    with tempfile.TemporaryFile(dir=path):
        pass
    config = Path(config or config_file())
    config.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=config.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(dict(folder=str(path.resolve())), stream)
    try:
        temporary.replace(config)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path.resolve())
