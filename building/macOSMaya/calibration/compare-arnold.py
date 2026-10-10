# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""Compare a MoonRay calibration render with Arnold's, pixel by pixel.

Run with the MoonRay environment's Python (building/macOSLocal/setup.sh: it has
OpenImageIO and numpy):
    python compare-arnold.py camera <light>.ass <moonray-dump>.rdla <out>.rdla
        Write <out>.rdla: the RDL MoonRay received (HDMOONRAY_RDLA_OUTPUT from the
        Maya viewport) re-framed with Arnold's render camera from the MtoA .ass
        (fov, matrix, resolution, pixel aspect), so both images cover the same
        pixels. Then render it: moonray -in <out>.rdla -out <out>.exr
    python compare-arnold.py ratio <moonray>.exr <arnold>.exr
        Print MoonRay/Arnold over pixels both cover, leaving out the rows of
        Arnold's licence watermark (unlicensed kick stamps "arnold" across the
        middle of the image; it once made area lights look 30% too dark).
See NOTES/GOTCHAS.md §29.
"""
import math
import re
import sys


def ass_camera(path):
    text = open(path).read()
    block = text[text.index("persp_camera"):]
    block = block[:block.index("}")]
    matrix = [float(v) for v in re.search(r"matrix\s+([-\d.e\s]+?)\s+[a-z_]", block).group(1).split()][:16]
    fov = float(re.search(r"\bfov ([-\d.e]+)", block).group(1))
    options = text[text.index("options"):]
    options = options[:options.index("}")]
    xres = int(re.search(r"\bxres (\d+)", options).group(1))
    yres = int(re.search(r"\byres (\d+)", options).group(1))
    aspect = re.search(r"\bpixel_aspect_ratio ([-\d.e]+)", options)
    return matrix, fov, xres, yres, float(aspect.group(1)) if aspect else 1.0


def camera(ass, dump, out, samples=8):
    matrix, fov, xres, yres, pixel_aspect = ass_camera(ass)
    focal = 50.0
    aperture = 2 * focal * math.tan(math.radians(fov / 2))
    s = open(dump).read()
    s = re.sub(r'\["image_width"\] = \d+', f'["image_width"] = {xres}', s)
    s = re.sub(r'\["image_height"\] = \d+', f'["image_height"] = {yres}', s)
    s = re.sub(r'\["camera"\] = PerspectiveCamera\("[^"]*"\)', '["camera"] = PerspectiveCamera("arnoldCamera")', s)
    s = re.sub(r'\["pixel_samples"\] = \d+,?', "", s)
    s = s.replace("SceneVariables {", f'SceneVariables {{\n    ["pixel_samples"] = {samples},', 1)
    s += (f'\nPerspectiveCamera("arnoldCamera") {{\n'
          f'    ["node_xform"] = Mat4({", ".join(repr(v) for v in matrix)}),\n'
          f'    ["focal"] = {focal}, ["film_width_aperture"] = {aperture!r},\n'
          f'    ["pixel_aspect_ratio"] = {pixel_aspect!r}, ["near"] = 0.1,\n}}\n')
    open(out, "w").write(s)
    print(f"wrote {out}: {xres}x{yres}, fov {fov}, pixel aspect {pixel_aspect}")


def ratio(moonray, arnold):
    import numpy as np
    import OpenImageIO as oiio

    def load(path):
        image = oiio.ImageInput.open(path)
        if image is None:
            sys.exit(f"cannot read {path}")
        pixels = np.asarray(image.read_image(format=oiio.FLOAT))
        image.close()
        return pixels

    m, a = load(moonray), load(arnold)
    m3, a3 = m[..., :3].mean(-1), a[..., :3].mean(-1)
    covered = (m[..., 3] > 0.5) & (a[..., 3] > 0.5)
    # The watermark: lit Arnold pixels away from the object (its anti-aliased
    # edge is lit too, so grow the object by a few pixels first). Drop its rows.
    near = covered | (a[..., 3] > 0.01)
    for _ in range(4):
        near = near | np.roll(near, 1, 0) | np.roll(near, -1, 0) | np.roll(near, 1, 1) | np.roll(near, -1, 1)
    watermark = (~near) & (a3 > 0.01)
    rows = np.where(watermark.any(axis=1))[0]
    keep = covered & (a3 > 1e-5)
    if rows.size:
        keep[rows.min():rows.max() + 1, :] = False
    if not keep.any():
        sys.exit("no pixels left to compare")
    r = m3[keep] / a3[keep]
    print(f"MoonRay/Arnold: median {np.median(r):.3f}  overall {m3[keep].mean() / a3[keep].mean():.3f}  "
          f"p5 {np.percentile(r, 5):.3f}  p95 {np.percentile(r, 95):.3f}  ({keep.sum()} pixels)")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "camera":
        camera(*sys.argv[2:5])
    elif len(sys.argv) == 4 and sys.argv[1] == "ratio":
        ratio(*sys.argv[2:4])
    else:
        sys.exit(__doc__)
