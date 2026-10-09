"""Small source-image previews, decoded only in the native image process."""
from pathlib import Path


def make_thumbnail(path, destination, raw=False, size=256):
    import numpy as np
    import OpenImageIO as oiio
    size = max(1, min(2048, int(size)))
    cache = oiio.ImageCache()
    cache.attribute("max_memory_MB", 64.)
    cache.attribute("autotile", 64)
    source = oiio.ImageBuf(path)
    if source.has_error:
        raise ValueError(source.geterror())
    original = source.spec()
    if original.deep or original.depth > 1:
        raise ValueError("Only flat 2D image textures have thumbnails.")
    width, height = original.width, original.height
    if width <= 0 or height <= 0:
        raise ValueError("Texture has no image pixels.")
    # Choose an existing mip level for tiled textures before filtering down.
    level = 0
    while max(source.spec().width, source.spec().height) > 2 * size and level + 1 < source.nmiplevels:
        level += 1
        source = oiio.ImageBuf(path, 0, level)
    spec = source.spec()
    ratio = min(1., size / max(width, height))
    w, h = max(1, round(width * ratio)), max(1, round(height * ratio))
    channels = list(spec.channelnames)
    aliases = {name.rsplit(".", 1)[-1].upper(): i for i, name in enumerate(channels)}
    alpha = spec.alpha_channel
    rgb = next(([aliases[a] for a in axes] for axes in (("R", "G", "B"), ("RED", "GREEN", "BLUE"))
                if all(a in aliases for a in axes)), [i for i in range(spec.nchannels) if i != alpha][:3] or [alpha])
    if not rgb:
        raise ValueError("Texture has no image channels.")
    # Channel selection before resizing bounds the temporary image to RGB even
    # when an EXR contains hundreds of unrelated AOVs.
    selected = oiio.ImageBufAlgo.channels(source, tuple(rgb))
    selected.set_origin(0, 0, 0)
    selected.set_full(0, selected.spec().width, 0, selected.spec().height, 0, 1)
    small = oiio.ImageBufAlgo.resize(selected, roi=oiio.ROI(0, w, 0, h, 0, 1, 0, selected.nchannels), nthreads=2)
    if small.has_error:
        raise ValueError(small.geterror())
    pixels = np.asarray(small.get_pixels(oiio.FLOAT))
    data = pixels[:, :, :len(rgb)]
    if len(rgb) == 1:
        data = np.repeat(data, 3, axis=2)
    elif len(rgb) == 2:
        data = np.concatenate((data, np.zeros_like(data[:, :, :1])), axis=2)
    colorspace = original.get_string_attribute("oiio:ColorSpace").lower()
    linear = colorspace in ("linear", "scene_linear", "lin_rec709") or (not colorspace and Path(path).suffix.lower() in (".exr", ".hdr"))
    if linear and not raw:
        data = np.maximum(data, 0)
        data = np.where(data <= .0031308, data * 12.92, 1.055 * data ** (1 / 2.4) - .055)
    data = np.ascontiguousarray((np.clip(np.nan_to_num(data, nan=0, posinf=1, neginf=0), 0, 1) * 255).astype(np.uint8))
    output = oiio.ImageOutput.create(str(destination))
    if not output or not output.open(str(destination), oiio.ImageSpec(w, h, data.shape[2], oiio.UINT8)):
        raise ValueError("Could not create texture thumbnail.")
    try:
        if not output.write_image(data):
            raise ValueError(output.geterror())
    finally:
        output.close()
    return dict(width=width, height=height, image=str(destination))
