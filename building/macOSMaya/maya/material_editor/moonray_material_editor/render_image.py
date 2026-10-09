"""Display conversion for color, vector, and scalar render outputs."""
def convert_preview(source, destination, view="beauty"):
    import numpy as np
    import OpenImageIO as oiio
    image = oiio.ImageInput.open(str(source))
    if not image:
        raise RuntimeError(f"Renderer did not produce {source}")
    data = np.asarray(image.read_image(format=oiio.FLOAT))
    names = list(image.spec().channelnames)
    image.close()
    if all(c in names for c in ("R", "G", "B")):
        rgb = data[:, :, [names.index(c) for c in ("R", "G", "B")]]
    elif data.shape[2] >= 3:
        rgb = data[:, :, :3]
    elif data.shape[2] == 2 and view == "roughness":
        rgb = np.repeat(np.mean(data, axis=2, keepdims=True), 3, axis=2)
    elif data.shape[2] == 2:
        rgb = np.concatenate([data, np.zeros_like(data[:, :, :1])], axis=2)
    else:
        rgb = np.repeat(data[:, :, :1], 3, axis=2)
    if view == "normal":
        rgb = rgb * .5 + .5
    elif view == "depth":
        valid = np.isfinite(rgb[:, :, 0]) & (rgb[:, :, 0] > 0) & (rgb[:, :, 0] < 1e20)
        if valid.any():
            low, high = np.percentile(rgb[:, :, 0][valid], [1, 99])
            rgb = 1 - np.clip((rgb - low) / max(high - low, 1e-6), 0, 1)
            rgb[~valid] = 0
    elif view in {"beauty", "albedo"}:
        rgb = np.maximum(rgb, 0)
        rgb = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - .055)
    rgb = np.nan_to_num(rgb, nan=0, posinf=1, neginf=0)
    output = oiio.ImageOutput.create(str(destination))
    if not output.open(str(destination), oiio.ImageSpec(rgb.shape[1], rgb.shape[0], 3, oiio.UINT8)):
        raise RuntimeError(output.geterror())
    pixels = np.ascontiguousarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    if not output.write_image(pixels):
        error = output.geterror()
        output.close()
        raise RuntimeError(error)
    if not output.close():
        raise RuntimeError(output.geterror())
