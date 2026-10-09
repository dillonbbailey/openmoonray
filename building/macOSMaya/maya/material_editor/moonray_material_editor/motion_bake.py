"""Directional and rotational map baking using native camera motion blur."""
import math


def motion_options(options):
    if not isinstance(options, dict):
        raise ValueError("Choose motion blur settings.")
    kind = options.get("type", "rotational")
    if kind not in ("rotational", "directional"):
        raise ValueError("Choose rotational or directional blur.")
    samples = options.get("samples", 16)
    if type(samples) is not int or not 1 <= samples <= 32:
        raise ValueError("Pixel samples per axis must be between 1 and 32.")
    if kind == "directional":
        direction, distance = options.get("direction", 0.), options.get("distance", .1)
        if type(direction) not in (int, float) or not math.isfinite(direction) or not 0 <= direction <= 360:
            raise ValueError("Blur direction must be between 0 and 360 degrees.")
        if type(distance) not in (int, float) or not math.isfinite(distance) or not 0 <= distance <= 1:
            raise ValueError("Blur distance must be between 0 and 1 UV unit.")
        return dict(type=kind, direction=float(direction), distance=float(distance), samples=samples)
    angle = options.get("angle", 30.)
    center = options.get("center", [.5, .5])
    if type(angle) not in (int, float) or not math.isfinite(angle) or not 0 <= angle <= 3600:
        raise ValueError("Blur angle must be between 0 and 3600 degrees (10 rotations).")
    if (not isinstance(center, (list, tuple)) or len(center) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) or not -10 <= v <= 10 for v in center)):
        raise ValueError("Blur center U and V must be between -10 and 10.")
    return dict(type=kind, angle=float(angle), center=list(center), samples=samples)


def shutter_intervals(angle):
    # Camera quaternions interpolate along the shortest arc. Keep each exposure
    # below 180 degrees, then average equal-duration exposures in linear RGB.
    count = max(1, math.ceil(angle / 120))
    return [(-angle / 2 + angle * i / count, -angle / 2 + angle * (i + 1) / count)
            for i in range(count)]


def rotation_exposures(angle):
    if angle <= 360:
        intervals = shutter_intervals(angle)
        return [(start, end, 1/len(intervals)) for start, end in intervals]
    # Full revolutions revisit the same static map. Render one circle and give
    # it the duration of all complete turns, retaining the remainder's phase.
    turns, remainder = divmod(angle, 360)
    result = [(start, end, turns*(end-start)/angle) for start, end in shutter_intervals(360)]
    if remainder:
        count = math.ceil(remainder/120)
        start = -angle/2 + turns*360
        result += [(start + remainder*i/count, start + remainder*(i+1)/count, remainder/count/angle)
                   for i in range(count)]
    return result


def motion_scene(options, start, end, output):
    from .native import quote, value
    directional = options["type"] == "directional"
    if directional:
        px = py = 0.
        radians = math.radians(options["direction"])
        dx, dy = math.cos(radians), math.sin(radians)
        rx, ry = 1.1 + abs(dx)*options["distance"], 1.1 + abs(dy)*options["distance"]
    else:
        px, py = (2 * component - 1 for component in options["center"])
        # Cover every rotated image corner, including reconstruction filtering.
        rx = ry = max(math.hypot(x - px, y - py) for x in (-1, 1) for y in (-1, 1)) + .1
    vertices = [[px-rx, py-ry, 0], [px+rx, py-ry, 0], [px-rx, py+ry, 0], [px+rx, py+ry, 0]]
    uv = [[(vertices[i][0]+1)/2, (vertices[i][1]+1)/2] for i in (0, 1, 3, 2)]

    def transform(angle):
        if directional:
            return value("Mat4d", [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [2*angle*dx, 2*angle*dy, 3, 1]])
        radians = math.radians(angle)
        c, s = math.cos(radians), math.sin(radians)
        # A fixed camera origin at the pivot avoids linear translation of an
        # orbiting camera. Film offsets put the pivot at its original UV pixel.
        return value("Mat4d", [[c, s, 0, 0], [-s, c, 0, 0], [0, 0, 1, 0], [px, py, 3, 1]])

    return f'''
preview_object {{
    ["vertex_list_0"] = {value("Vec3fVector", vertices)},
    ["uv_list"] = {value("Vec2fVector", uv)},
}}
bake_motion_camera = OrthographicCamera("/Bake/MotionCamera") {{
    ["film_width_aperture"] = 2,
    ["horizontal_film_offset"] = {-px!r},
    ["vertical_film_offset"] = {-py!r},
    ["near"] = 0.1,
    ["dof"] = false,
    ["mb_shutter_open"] = 0,
    ["mb_shutter_close"] = 1,
    ["mb_shutter_bias"] = 0,
    ["node_xform"] = blur({transform(start)}, {transform(end)}),
}}
SceneVariables {{
    ["camera"] = bake_motion_camera,
    ["enable_motion_blur"] = {"true" if start != end else "false"},
    ["motion_steps"] = {{0, 1}},
    ["output_file"] = {quote(output)},
}}
'''


def average_exposures(paths, output, size, weights):
    """Accumulate in strips so 8K bakes don't require several full float images."""
    import numpy as np
    import OpenImageIO as oiio
    from contextlib import ExitStack
    with ExitStack() as stack:
        readers = []
        for path in paths:
            reader = oiio.ImageInput.open(str(path))
            if not reader:
                raise ValueError("Cannot read motion-blur exposure: " + str(path))
            stack.callback(reader.close)
            spec = reader.spec()
            if (spec.width, spec.height) != (size, size) or spec.nchannels < 3:
                raise ValueError("Motion-blur exposure has unexpected dimensions or channels.")
            readers.append(reader)
        writer = oiio.ImageOutput.create(str(output))
        if not writer:
            raise ValueError("Cannot create the combined motion-blur image.")
        stack.callback(writer.close)
        spec = oiio.ImageSpec(size, size, 3, oiio.FLOAT)
        spec.attribute("oiio:ColorSpace", "Linear")
        if not writer.open(str(output), spec):
            raise ValueError(writer.geterror())
        for y in range(0, size, 64):
            end = min(size, y + 64)
            total = np.zeros((end-y, size, 3), dtype=np.float32)
            for reader, weight in zip(readers, weights):
                chunk = reader.read_scanlines(y, end, 0, 0, 3, oiio.FLOAT)
                if chunk is None or not np.isfinite(chunk).all():
                    raise ValueError("Cannot read finite RGB pixels from a motion-blur exposure.")
                total += chunk * weight
            if not writer.write_scanlines(y, end, 0, total):
                raise ValueError(writer.geterror())
        if not writer.close():
            raise ValueError(writer.geterror())
    return output


def bake_motion_map(data, directory, size):
    from .map_preview import prepare_textures, preview_graph
    from .model import Catalog
    from .native import create_scene
    from .worker import run_native_renderer
    options = motion_options(data["motion_blur"])
    graph, _ = preview_graph(Catalog(), data["graph"], data["node"])
    graph.data["preview"]["samples"] = options["samples"]
    prepare_textures(graph, directory)
    scene = directory / "motion.rdla"
    create_scene(graph, scene, directory, size=size)
    base = scene.read_text()
    exposures = (rotation_exposures(options["angle"]) if options["type"] == "rotational" else
                 [(-options["distance"]/2, options["distance"]/2, 1.)])
    outputs = []
    for index, (start, end, _) in enumerate(exposures):
        output = directory / f"exposure-{index}.exr"
        scene.write_text(base + motion_scene(options, start, end, output))
        print(f"{options['type'].capitalize()} blur exposure {index+1}/{len(exposures)}", flush=True)
        run_native_renderer(["moonray", "-in", str(scene), "-exec_mode", "scalar", "-threads", "2"],
                            progress_step=1, progress_range=(int(index*95/len(exposures)), int((index+1)*95/len(exposures))))
        outputs.append(output)
    if len(outputs) == 1:
        return outputs[0]
    return average_exposures(outputs, directory / "motion-blur.exr", size, [weight for _, _, weight in exposures])
