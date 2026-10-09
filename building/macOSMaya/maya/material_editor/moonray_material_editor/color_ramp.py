"""Native color-ramp metadata and a small CPU preview of MoonRay ramp curves."""
import bisect
import colorsys
import math


INTERPOLATIONS = ("Constant", "Linear", "Exponential up", "Exponential down", "Smooth",
                  "Catmull-Rom", "Monotone cubic")
# ColorRampControl in the installed renderer evaluates at most ten points.
MAX_STOPS = 10


def ramp_color_inputs(catalog, shader):
    """Optional per-stop bindings supplied by the patched native RampMap."""
    names = [f"color_{i+1}" for i in range(MAX_STOPS)]
    attrs = catalog.attributes(shader)
    return names if shader == "RampMap" and all(attrs.get(name, {}).get("bindable") for name in names) else []


def remap_color_inputs(graph, node_id, order):
    """Keep connections and exposed sockets attached to their edited color stop."""
    node = graph.node(node_id)
    ports = ramp_color_inputs(graph.catalog, node["shader"])
    if not ports:
        return
    mapping = {ports[old]: ports[new] for new, old in enumerate(order) if old is not None}
    connections = []
    for connection in graph.data["connections"]:
        if connection["target"] == node_id and connection["input"] in ports:
            if connection["input"] not in mapping:
                continue
            connection["input"] = mapping[connection["input"]]
        connections.append(connection)
    graph.data["connections"] = connections
    node["ports"] = [mapping.get(port, port) for port in node["ports"] if port not in ports or port in mapping]
    node["ports"].extend(ports[i] for i, old in enumerate(order) if old is None)
    node["values"] = {mapping.get(name, name): value for name, value in node["values"].items()
                      if name not in ports or name in mapping}


def color_ramps(catalog, shader):
    structures = {}
    for name, attr in catalog.attributes(shader).items():
        meta = attr.get("metadata", {})
        if meta.get("structure_type") == "ramp_color":
            structures.setdefault(meta["structure_name"], {})[meta["structure_path"]] = name
    types = {"values": "RgbVector", "positions": "FloatVector", "interpolation_types": "IntVector"}
    return {name: fields for name, fields in structures.items()
            if all(key in fields and catalog.attributes(shader)[fields[key]]["attrType"] == kind
                   for key, kind in types.items())}


def read_stops(values, positions, interpolations):
    if not len(values) == len(positions) == len(interpolations):
        raise ValueError("Colors, positions and interpolation arrays must have the same length.")
    if len(values) > MAX_STOPS:
        raise ValueError(f"The renderer supports up to {MAX_STOPS} color stops.")
    if any(mode not in range(len(INTERPOLATIONS)) for mode in interpolations):
        raise ValueError("The ramp contains an unsupported interpolation mode.")
    return [dict(position=p, color=list(c), interpolation=i) for c, p, i in zip(values, positions, interpolations)]


def display_color(rgb):
    return [max(0., min(1., v * 12.92 if v <= .0031308 else 1.055 * v ** (1 / 2.4) - .055)) for v in rgb]


def linear_color(rgb):
    return [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]


def color_coordinates(rgb, space):
    """Match MoonRay's HSV/HSL handling of HDR and negative components."""
    low, high = min(rgb), max(rgb)
    chroma = high - low
    hue = colorsys.rgb_to_hsv(*(v - low for v in rgb))[0] if chroma > 1e-6 else 0.
    if space == 1:
        return (hue, chroma / high, high) if abs(high) > 1e-6 else (0., 0., high)
    total = high + low
    if chroma <= 1e-6:
        saturation = 0.
    elif total > 1:
        saturation = 1. if abs(total - 2) <= 1e-6 else abs(chroma / (2 - total))
    else:
        saturation = chroma if total <= 0 else chroma / total
    return hue, saturation, total / 2


def monotone_slopes(xs, ys):
    """Pairwise slope limiting used by MoonRay's RampControl.cc."""
    slopes = [0.] * len(xs)
    delta = [(b - a) / (v - u) if v != u else 0. for u, v, a, b in zip(xs, xs[1:], ys, ys[1:])]
    if len(xs) <= 2:
        return slopes
    for i in range(0, len(xs) - 1, 2):
        d = delta[i]
        if abs(d) < 1e-6:
            continue
        left = d if i == 0 else (delta[i-1] + d) / 2 if delta[i-1] * d > 0 else 0.
        right = d if i + 2 == len(xs) else (delta[i+1] + d) / 2 if delta[i+1] * d > 0 else 0.
        length = math.hypot(left / d, right / d)
        scale = min(1., 3 / length) if length else 1.
        slopes[i], slopes[i+1] = left * scale, right * scale
    if len(xs) % 2:
        slopes[-1] = delta[-1]
    return slopes


def sample_ramp(stops, position, space=0):
    if not stops:
        return [0., 0., 0.]
    ordered = sorted(stops, key=lambda stop: stop["position"])
    xs = [stop["position"] for stop in ordered]
    rgb = [stop["color"] for stop in ordered]
    modes = [stop["interpolation"] for stop in ordered]
    if len(stops) == 1 or position < xs[0]:
        return list(rgb[0])
    if position > xs[-1]:
        return list(rgb[-1])
    # MoonRay falls back to RGB when any segment uses Catmull-Rom.
    space = 0 if 5 in modes else space
    colors = [color_coordinates(c, space) for c in rgb] if space in (1, 2) else rgb
    left = min(len(xs) - 2, max(0, bisect.bisect_right(xs, position) - 1))
    span = xs[left+1] - xs[left]
    t = (position - xs[left]) / span if abs(span) > 1e-6 else 0.
    mode = modes[left] if abs(span) > 1e-6 else 0
    a, b = list(colors[left]), list(colors[left+1])
    if space and mode < 5:
        if a[0] + 1 - b[0] < .5:
            a[0] += 1
        elif b[0] + 1 - a[0] < .5:
            b[0] += 1
    if mode < 5:
        weight = (0., t, t*t, 1-(1-t)**2, math.sin(t * math.pi / 2))[mode]
        result = [u + weight * (v-u) for u, v in zip(a, b)]
    elif mode == 5:
        p, q = colors[max(0, left-1)], colors[min(len(xs)-1, left+2)]
        result = [.5 * (2*u + (-w+v)*t + (2*w-5*u+4*v-z)*t*t + (-w+3*u-3*v+z)*t**3)
                  for w, u, v, z in zip(p, a, b, q)]
    else:
        result = []
        for channel, (u, v) in enumerate(zip(a, b)):
            slopes = monotone_slopes(xs, [c[channel] for c in rgb])
            result.append((2*t**3-3*t*t+1)*u + span*(t**3-2*t*t+t)*slopes[left]
                          + (-2*t**3+3*t*t)*v + span*(t**3-t*t)*slopes[left+1])
    if space == 1:
        return list(colorsys.hsv_to_rgb(result[0] % 1, result[1], result[2]))
    if space == 2:
        return list(colorsys.hls_to_rgb(result[0] % 1, result[2], result[1]))
    return result
