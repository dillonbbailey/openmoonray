"""Pixel regions use top-left, half-open coordinates throughout the editor."""
import os
from pathlib import Path


def validate_region(region, width, height):
    if region is None:
        return None
    if (not isinstance(region, (list, tuple)) or len(region) != 4
            or any(type(v) is not int for v in region)):
        raise ValueError("Render region must contain four whole pixel coordinates.")
    x0, y0, x1, y1 = region
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("Render region must be inside the render resolution. Clear it or select it again.")
    return list(region)


def native_region(region, width, height):
    x0, y0, x1, y1 = validate_region(region, width, height)
    return [x0, height - y1, x1, height - y0]


def preserve_display_region(path, base, region, stride=1):
    """Keep displayed pixels stable even for data-dependent depth normalization."""
    if not base or not region:
        return
    import numpy as np
    import OpenImageIO as oiio
    image, previous = oiio.ImageBuf(str(path)), oiio.ImageBuf(str(base))
    pixels, merged = image.get_pixels(oiio.UINT8), previous.get_pixels(oiio.UINT8)
    if image.has_error or previous.has_error or pixels is None or merged is None:
        raise ValueError("Cannot read the previous region display image.")
    if pixels.shape != merged.shape:
        raise ValueError("The region display resolution changed. Clear the region first.")
    # Display conversion samples original pixels [::stride, ::stride].
    x0, y0, x1, y1 = [(v + stride - 1) // stride for v in region]
    merged[y0:y1, x0:x1] = pixels[y0:y1, x0:x1]
    output = oiio.ImageOutput.create(str(path))
    if not output or not output.open(str(path), oiio.ImageSpec(image.spec())):
        raise ValueError("Cannot write the region display image.")
    try:
        if not output.write_image(np.ascontiguousarray(merged)):
            raise ValueError(output.geterror())
    finally:
        output.close()


def merge_region(path, base, region, width, height):
    """Retain old pixels outside a rendered region, in every flat EXR part/AOV.

    MoonRay writes a cropped data window for a sub-viewport. Expand it back to
    the full frame without changing camera framing. New channels start black;
    channels present in the old image retain their original values outside ROI.
    The file is replaced only after a complete, losslessly compressed write.
    """
    region = validate_region(region, width, height)
    if region is None:
        return
    import numpy as np
    import OpenImageIO as oiio

    def read(source):
        image = oiio.ImageInput.open(str(source))
        if not image:
            raise ValueError(f"Cannot read region image: {source}")
        parts = []
        try:
            index = 0
            while image.seek_subimage(index, 0):
                spec = oiio.ImageSpec(image.spec())
                if spec.deep:
                    raise ValueError("Render regions require flat EXR images.")
                pixels = image.read_image(index, 0, 0, spec.nchannels, oiio.FLOAT)
                if pixels is None:
                    raise ValueError(image.geterror())
                parts.append((spec, np.asarray(pixels)))
                index += 1
        finally:
            image.close()
        return parts

    old = read(base) if base else []
    for spec, _ in old:
        if (spec.x, spec.y, spec.width, spec.height) != (0, 0, width, height):
            raise ValueError("The previous image has a different resolution. Clear the region and render a full frame first.")
    parts = []
    for index, (spec, pixels) in enumerate(read(path)):
        if (spec.full_x, spec.full_y, spec.full_width, spec.full_height) != (0, 0, width, height):
            raise ValueError("The rendered image window differs from the region resolution. Use Res 1 and the default aperture/region window, then render a full frame first.")
        merged = np.zeros((height, width, spec.nchannels), dtype=np.float32)
        name = spec.get_string_attribute("oiio:subimagename")
        candidates = [(s, p) for s, p in old if s.get_string_attribute("oiio:subimagename") == name]
        previous = candidates[0] if len(candidates) == 1 else old[index] if not name and index < len(old) else None
        if previous:
            old_spec, old_pixels = previous
            for channel, key in enumerate(spec.channelnames):
                if key in old_spec.channelnames:
                    merged[:, :, channel] = old_pixels[:, :, list(old_spec.channelnames).index(key)]
        x0, y0, x1, y1 = region
        x0, y0 = max(x0, spec.x), max(y0, spec.y)
        x1, y1 = min(x1, spec.x + spec.width), min(y1, spec.y + spec.height)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("The renderer image does not overlap the selected region.")
        merged[y0:y1, x0:x1] = pixels[y0-spec.y:y1-spec.y, x0-spec.x:x1-spec.x]
        spec.x = spec.y = spec.full_x = spec.full_y = 0
        spec.width = spec.full_width = width
        spec.height = spec.full_height = height
        # FLOAT avoids quantizing retained pixels when an output changes HALF/FLOAT.
        spec.set_format(oiio.FLOAT)
        spec.channelformats = ()
        spec.attribute("compression", "zip")
        parts.append((spec, merged))
    target = Path(path).with_name(Path(path).stem + ".region.exr")
    output = oiio.ImageOutput.create(str(target))
    try:
        if not output or not output.open(str(target), tuple(s for s, _ in parts)):
            raise ValueError("Cannot create region EXR.")
        for index, (spec, pixels) in enumerate(parts):
            if index and not output.open(str(target), spec, "AppendSubimage"):
                raise ValueError(output.geterror())
            if not output.write_image(np.ascontiguousarray(pixels)):
                raise ValueError(output.geterror())
        if not output.close():
            raise ValueError(output.geterror())
        os.replace(target, path)
    finally:
        if output:
            output.close()
        target.unlink(missing_ok=True)
