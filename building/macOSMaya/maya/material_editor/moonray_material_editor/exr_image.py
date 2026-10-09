"""EXR inspection and display conversion in the isolated OpenImageIO runtime."""
from pathlib import Path

from .exr_channels import resolve_selection


def inspect_exr(path):
    import OpenImageIO as oiio
    image = oiio.ImageInput.open(str(path))
    if not image:
        raise ValueError("Cannot open EXR: " + str(path))
    parts, views = [], []
    ovrtx_outputs = False
    try:
        index = 0
        while image.seek_subimage(index, 0):
            spec = image.spec()
            ovrtx_outputs |= bool(spec.get_string_attribute("moonlab:ovrtx:outputs"))
            channels = list(spec.channelnames)
            name = spec.get_string_attribute("oiio:subimagename") or f"Part {index + 1}"
            parts.append(dict(index=index, name=name, width=spec.width, height=spec.height,
                              x=spec.x, y=spec.y, channels=channels, deep=bool(spec.deep)))
            groups = {}
            for channel_index, channel in enumerate(channels):
                prefix, _, component = channel.rpartition(".")
                groups.setdefault(prefix, {})[component.upper()] = channel_index
            for prefix, components in groups.items():
                for axes in (("R", "G", "B"), ("X", "Y", "Z"), ("U", "V")):
                    if all(axis in components for axis in axes):
                        label = prefix or ("RGB" if axes[0] == "R" else "Vector")
                        hint = "signed" if "normal" in label.lower() or axes[0] == "X" else "srgb" if axes[0] == "R" else "raw"
                        views.append(dict(part=index, channels=[components[a] for a in axes], label=label, display=hint))
                        break
            for channel_index, channel in enumerate(channels):
                hint = "normalize" if channel.lower() in ("z", "depth", "depth.z") else "raw"
                views.append(dict(part=index, channels=[channel_index], label=channel, display=hint))
            index += 1
    finally:
        image.close()
    if not parts:
        raise ValueError("The EXR has no image parts.")
    if len(parts) > 1:
        for view in views:
            view["label"] = parts[view["part"]]["name"] + " / " + view["label"]
    result = dict(path=str(Path(path).resolve()), parts=parts, views=views)
    if ovrtx_outputs:
        from .ovrtx_outputs import apply_display_hints
        apply_display_hints(result)
    return result


def display_exr(path, destination, view=None, display="auto", exposure=0, *, selection=None, metadata=None):
    import numpy as np
    import OpenImageIO as oiio
    resolved = resolve_selection(metadata or inspect_exr(path), selection) if selection is not None else None
    if resolved:
        view = resolved["view"]
    image = oiio.ImageInput.open(str(path))
    if not image:
        raise ValueError("Cannot open EXR: " + str(path))
    try:
        def read(part, channels):
            if not image.seek_subimage(part, 0):
                raise ValueError("The selected EXR part is unavailable.")
            spec = image.spec()
            if spec.deep:
                raise ValueError("Deep EXR display is not supported. The original file can still be saved.")
            if not channels or min(channels) < 0 or max(channels) >= spec.nchannels:
                raise ValueError("Invalid EXR channel selection.")
            first, last = min(channels), max(channels) + 1
            pixels = image.read_image(part, 0, first, last, oiio.FLOAT)
            if pixels is None:
                raise ValueError(image.geterror() or "Could not read EXR pixels.")
            return np.asarray(pixels)[:, :, [channel - first for channel in channels]], spec

        data, spec = read(view["part"], view["channels"])
        info = dict(path=str(Path(path).resolve()), part=view["part"], x=spec.x, y=spec.y,
                    width=spec.width, height=spec.height, probe_channels=[], warning="")
        matte_data = None
        if resolved:
            info["probe_channels"] = [dict(part=view["part"], name=m["name"], label=m["leaf"])
                                      for m in resolved["layer"]["members"]]
            matte = resolved["matte"]
            if matte:
                info["probe_channels"].append(dict(part=matte["part"], name=matte["name"], label="Matte"))
            if resolved["component"] == "A" or resolved["overlay"]:
                matte_data = np.zeros((spec.height, spec.width, 1), dtype=np.float32)
                if matte:
                    values, mask_spec = read(matte["part"], [matte["channel"]])
                    # Multipart masks align in EXR pixel coordinates, with zero
                    # outside their data window, never stretched to fit the image.
                    x0, y0 = max(spec.x, mask_spec.x), max(spec.y, mask_spec.y)
                    x1 = min(spec.x + spec.width, mask_spec.x + mask_spec.width)
                    y1 = min(spec.y + spec.height, mask_spec.y + mask_spec.height)
                    if x1 > x0 and y1 > y0:
                        matte_data[y0-spec.y:y1-spec.y, x0-spec.x:x1-spec.x] = values[
                            y0-mask_spec.y:y1-mask_spec.y, x0-mask_spec.x:x1-mask_spec.x]
                else:
                    info["warning"] = "No matte channel available; choose one in Matte."
            if resolved["component"] == "A":
                data = matte_data
    finally:
        image.close()
    if data.shape[2] == 1:
        data = np.repeat(data, 3, axis=2)
    elif data.shape[2] == 2:
        data = np.concatenate((data, np.zeros_like(data[:, :, :1])), axis=2)
    data = data[:, :, :3] * (2.0 ** float(exposure))
    mode = view.get("display", "raw") if display == "auto" else display
    if mode == "signed":
        data = data * 0.5 + 0.5
    elif mode == "normalize":
        valid = np.isfinite(data) & (np.abs(data) < 1e20)
        if valid.any():
            low, high = np.percentile(data[valid], [1, 99])
            data = (data - low) / max(float(high - low), 1e-8)
        data[~valid] = 0
    elif mode == "srgb":
        data = np.maximum(data, 0)
        data = np.where(data <= .0031308, data * 12.92, 1.055 * data ** (1 / 2.4) - .055)
    elif mode != "raw":
        raise ValueError("Unknown display transform.")
    data = np.nan_to_num(data, nan=0, posinf=1, neginf=0)
    if resolved and resolved["overlay"]:
        weight = .5 * np.clip(np.nan_to_num(matte_data, nan=0, posinf=1, neginf=0), 0, 1)
        data = np.clip(data, 0, 1) * (1 - weight) + np.array([1., 0., 0.]) * weight
    # Display a bounded preview; the original EXR retains its full resolution.
    stride = max(1, int(np.ceil(max(data.shape[:2]) / 2048)))
    info["stride"] = stride
    pixels = np.ascontiguousarray((np.clip(data[::stride, ::stride], 0, 1) * 255).astype(np.uint8))
    output = oiio.ImageOutput.create(str(destination))
    if not output or not output.open(str(destination), oiio.ImageSpec(pixels.shape[1], pixels.shape[0], 3, oiio.UINT8)):
        raise ValueError("Could not create the display image.")
    try:
        if not output.write_image(pixels):
            raise ValueError(output.geterror())
    finally:
        output.close()
    return info


class PixelReader:
    """Bounded native image cache for exact, unprocessed source pixel queries."""
    def __init__(self):
        import OpenImageIO as oiio
        self.cache = oiio.ImageCache()
        self.cache.attribute("max_memory_MB", 64.)
        self.cache.attribute("autotile", 64)
        self.path = None
        self.images = {}

    def sample(self, path, x, y, channels):
        import math
        import OpenImageIO as oiio
        if path != self.path:
            self.images.clear()
            self.cache.invalidate_all(True)
            self.path = path
        result = []
        pixels = {}
        for channel in channels:
            part = channel["part"]
            if part not in self.images:
                self.images[part] = oiio.ImageBuf(path, part, 0)
            image = self.images[part]
            spec = image.spec()
            if image.has_error:
                raise ValueError(image.geterror())
            if spec.deep:
                raise ValueError("Deep EXR pixel inspection is not supported.")
            if channel["name"] not in spec.channelnames:
                raise ValueError("The selected channel is no longer available.")
            if part not in pixels:
                pixels[part] = image.getpixel(x, y, spec.z)
            value = pixels[part][list(spec.channelnames).index(channel["name"])]
            if image.has_error:
                raise ValueError(image.geterror())
            # Standard JSON, including an explicit representation of non-finite
            # data rather than replacing it with display-safe black or white.
            result.append(dict(label=channel["label"], name=channel["name"], part=part,
                               value=value if math.isfinite(value) else str(value)))
        return dict(x=x, y=y, values=result)
