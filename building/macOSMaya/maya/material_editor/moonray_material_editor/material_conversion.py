"""Reviewable, bounded material recipes shared by the host and USD worker.

MDL recipes transfer available literal parameters. They are starting points,
never an assertion that an arbitrary MDL program has been translated.
"""
import math
import re

from .model import Graph

DESTINATIONS = ("DwaBaseMaterial", "DwaSolidDielectricMaterial", "DwaMetalMaterial",
                "DwaRefractiveMaterial", "DwaEmissiveMaterial", "DwaFabricMaterial",
                "DwaVelvetMaterial_v2")

FLAKE_PORTS = (
    ("glitter_color_A", ("flake_color", "flakes_color", "flakes_tint")),
    ("glitter_roughness_A", ("flake_roughness", "flakes_roughness")),
    ("glitter_size_A", ("flake_size", "flakes_size")),
    ("glitter_density", ("flakes_density", "flake_density")),
    ("glitter_randomness", ("flakes_spread", "flake_spread", "flake_orientation_randomness")),
    ("glitter", ("flake_weight", "flakes_weight", "flake_layer_visibility", "flakes_opacity")),
    ("glitter_seed", ("flake_seed", "flakes_seed")),
)
FLAKE_ENABLE = ("enable_flakes", "enable_flake", "flakes_enabled")


def flake_values(source):
    values = source.get("values", {})
    controls = {}
    for port, names in FLAKE_PORTS:
        name = next((key for key in names if key in values), None)
        if name:
            controls[port] = name, values[name]
    return controls


def map_flakes(graph, node_id, source, used, mapped, notes):
    """Transfer literal flake controls; procedural patterns remain approximate."""
    if graph.node(node_id)["shader"] != "DwaBaseMaterial":
        return
    values, connections = source.get("values", {}), set(source.get("connections", []))
    controls = flake_values(source)
    enable = next((key for key in FLAKE_ENABLE if key in values or key in connections), None)
    if not controls and enable is None:
        return
    transferred = {}
    for port, (name, value) in controls.items():
        if name in connections:
            continue
        if port == "glitter_color_A" and type(value) in (float, int):
            value = [value] * 3
        if port == "glitter_seed" and type(value) is float and math.isfinite(value) and value.is_integer():
            value = int(value)
        if port in ("glitter_roughness_A", "glitter_randomness", "glitter", "glitter_size_A", "glitter_density"):
            if type(value) in (int, float) and math.isfinite(value):
                bounded = max(0., value)
                if port in ("glitter_roughness_A", "glitter_randomness", "glitter"):
                    bounded = min(1., bounded)
                if bounded != value:
                    notes.append(f"{name}: clamped from {value:g} to {bounded:g} for {port}.")
                value = bounded
        try:
            graph.set_value(node_id, port, value)
        except ValueError:
            notes.append(f"{name}: unsupported literal value; left unmapped.")
            continue
        transferred[port] = value
        used.add(name)
        mapped.append(f"{name} → {port}")
    # A switch explicitly set to false always wins over appearance defaults.
    enabled = any(port != "glitter_seed" for port in transferred)
    if enable:
        enabled = values.get(enable) if type(values.get(enable)) is bool and enable not in connections else False
        if type(values.get(enable)) is bool and enable not in connections:
            used.add(enable)
            mapped.append(f"{enable} → show_glitter")
        else:
            notes.append(f"{enable}: no literal boolean; glitter stays disabled until reviewed.")
    # MoonRay evaluates glitter in object space without requiring ref_P.
    graph.set_value(node_id, "glitter_space", 4)
    graph.set_value(node_id, "glitter_style_A_frequency", 1.)
    graph.set_value(node_id, "glitter_style_B_frequency", 0.)
    base = source.get("mdl_base", source.get("family", "")).split("::")[-1]
    size = transferred.get("glitter_size_A")
    density = transferred.get("glitter_density")
    if base == "OmniUber_Automotive" and size is not None and size > 0 and density is not None:
        units = source.get("meters_per_unit")
        if type(units) in (int, float) and math.isfinite(units) and units > 0:
            # OmniUber uses coordinates in meters, frequency 1492.5373 / size,
            # and flake_probability = density**2. Approximate that coverage in
            # MoonRay's surface distribution: diameter=cell width in object
            # units, linear density=sqrt(probability)/cell width. The patterns,
            # projection, orientation distribution and object scale still differ.
            width = size / 1492.537313432835 / units
            frequency = min(1., density) / width if width > 0 else math.inf
            # Both destinations are Float inputs; reject derived overflow/underflow.
            if 1.17549435e-38 <= width <= 3.40282347e38 and 0 <= frequency <= 3.40282347e38:
                graph.set_value(node_id, "glitter_size_A", width)
                graph.set_value(node_id, "glitter_density", frequency)
                size_name, density_name = controls['glitter_size_A'][0], controls['glitter_density'][0]
                mapped.remove(f"{size_name} → glitter_size_A")
                mapped.remove(f"{density_name} → glitter_density")
                mapped.append(f"{size_name} → glitter_size_A (OmniUber cell scale, {units:g} meters/unit)")
                mapped.append(f"{density_name} + {size_name} → glitter_density (approximate coverage / spacing)")
                notes.append("OmniUber flake size is converted from its meter-based noise scale to stage units. Density uses an approximate surface-coverage match; compare glitter size/density in a render, especially on scaled objects.")
            else:
                notes.append("Derived flake scale is outside MoonRay's floating-point range; size/density remain numeric starting values and need calibration.")
        else:
            notes.append("Stage units are unavailable; flake size/density remain numeric starting values and need calibration.")
    else:
        notes.append("Flake size/density are numeric starting values; their source units and procedural distribution are not calibrated to MoonRay.")
    if any(transferred.get(port, 1.) <= 0 for port in ("glitter", "glitter_density", "glitter_size_A")):
        enabled = False
    if base == "OmniUber_Automotive" and values.get("enable_coat") is False:
        enabled = False
        notes.append("OmniUber gates flakes with its coat layer; enable_coat=false keeps glitter disabled.")
    graph.set_value(node_id, "show_glitter", enabled)
    if not enable and enabled:
        mapped.append("Authored flake controls → show_glitter enabled")
    notes.append("Flakes use DwaBaseMaterial Glitter A in object space. Color, roughness and orientation spread are starting values; MDL flake noise, filtering and connected inputs are not reproduced.")


def recommendation(source):
    family = source["family"]
    name = family.lower()
    if source.get("native"):
        return None, "Already has a MoonRay surface; keep the original material."
    if source.get("missing"):
        return None, "The binding target is missing or is not a Material."
    if "project" in name or "environment" in name:
        return None, "Projection and environment blending need a manual rebuild."
    if "glass" in name or "refract" in name:
        return "DwaRefractiveMaterial", "Glass preset: start with refraction, tint, IOR and thin-wall settings."
    values = source.get("values", {})
    enabled = next((values[key] for key in FLAKE_ENABLE if key in values), None)
    if "flake" in name or "flaky" in name or enabled is True:
        return "DwaBaseMaterial", "Flake surface: transfer available flake controls to Glitter A; review procedural scale and density."
    if any(word in name for word in ("carpaint", "car_paint", "clearcoat", "carbon_fiber", "painted")):
        return "DwaBaseMaterial", "Layered paint/coat: transfer available flake controls to Glitter A; review coat and anisotropy."
    if any(word in name for word in ("chrome", "aluminum", "metal", "steel", "gold", "silver", "iron", "mirror")):
        return "DwaMetalMaterial", "Conductor preset: use metallic color, edge color and roughness."
    if name.startswith("light_") or "emissive" in name:
        return "DwaEmissiveMaterial", "Emissive preset: start with emission; compare intensity in a render."
    if "suede" in name or "velvet" in name:
        return "DwaVelvetMaterial_v2", "Soft fabric preset: use a velvet material and review the fiber response."
    if "cloth" in name or "fabric" in name:
        return "DwaFabricMaterial", "Fabric preset: review weave, normals and fiber colors after conversion."
    if family == "UsdPreviewSurface" or name.startswith(("omnipbr", "omnisurface")):
        return "DwaBaseMaterial", "Metallic/roughness surface: map base color, roughness, metallic and emission."
    if source.get("mdl"):
        return "DwaSolidDielectricMaterial", "MDL preset: a dielectric is a starting point; inspect the transferred parameters."
    return None, "No supported surface recipe. Rebuild this shader manually."


def recipe(catalog, source, destination=None):
    recommended, reason = recommendation(source)
    destination = destination or recommended
    notes = list(source.get("notes", []))
    if not recommended:
        return dict(status="Keep" if source.get("native") else "Manual", reason=reason,
                    mapped=[], unmapped=[], notes=notes, graph=None)
    if destination not in DESTINATIONS or destination not in catalog.shaders:
        raise ValueError("Choose an available MoonRay destination material.")
    graph = Graph(catalog)
    graph.rename_material(source["name"] + "_MOONRAY")
    node = graph.add(destination)
    node_id = node["id"]
    attrs = catalog.attributes(destination)
    values = source.get("values", {})
    used, mapped = set(), []

    def assign(port, names, transform=None):
        if port not in attrs:
            return
        for name in names:
            if name in values:
                value = values[name]
                if transform:
                    value = transform(value)
                graph.set_value(node_id, port, value)
                used.add(name)
                mapped.append(f"{name} → {port}")
                return value

    def color(value):
        return [float(value)] * 3 if isinstance(value, (int, float)) else value

    def flag(port, value):
        if port in attrs:
            graph.set_value(node_id, port, bool(value))

    # Prevent unrelated default lobes from silently adding light or a coat.
    for port in ("show_emission", "show_clearcoat", "show_fuzz", "show_glitter"):
        flag(port, False)
    color_names = ("diffuseColor", "diffuse_color_constant", "diffuse_reflection_color", "base_color", "diffuse_color")
    assign("albedo", color_names, color)
    # In metallic workflows the base color tints both diffuse and conductor lobes.
    if destination == "DwaBaseMaterial":
        assign("metallic_color", color_names, color)
    else:
        assign("metallic_color", ("metallic_color", "specular_reflection_color") +
               (() if destination != "DwaMetalMaterial" else color_names), color)
    assign("metallic_edge_color", ("metallic_edge_color",), color)
    assign("roughness", ("roughness", "roughness_constant", "specular_reflection_roughness", "roughness_u"))
    assign("metallic", ("metallic", "metallic_constant", "metalness"))
    assign("refractive_index", ("ior", "specular_reflection_ior"))
    assign("specular", ("specular_level", "specular_reflection_weight"))
    assign("anisotropy", ("anisotropy", "specular_reflection_anisotropy"))
    assign("presence", ("opacity", "opacity_constant"))
    assign("thin_geometry", ("thin_walled",))
    assign("transmission_color", ("transmission_color", "specular_transmission_color"), color)
    assign("transmission", ("specular_transmission_weight",))
    coat = assign("clearcoat", ("clearcoat", "coat_weight"))
    flag("show_clearcoat", bool(coat))
    assign("show_clearcoat", ("enable_coat", "enable_clearcoat"))
    assign("clearcoat_roughness", ("clearcoatRoughness", "coat_roughness"))
    assign("clearcoat_refractive_index", ("coat_ior",))
    emission = assign("emission", ("emissiveColor", "emissive_color", "emission_color"), color)
    if emission is not None:
        for name in ("emissive_intensity", "emission_intensity"):
            if name in values:
                emission = [v * float(values[name]) for v in emission]
                graph.set_value(node_id, "emission", emission)
                used.add(name)
                mapped.append(f"{name} → emission multiplier")
        flag("show_emission", any(emission))
    elif destination == "DwaEmissiveMaterial":
        flag("show_emission", True)
        notes.append("Emission color/intensity not available; the destination default needs review.")
    assign("show_emission", ("enable_emission",))
    map_flakes(graph, node_id, source, used, mapped, notes)

    textures = (
        ("albedo", ("diffuse_texture", "diffuse_reflection_color_image", "diffuse_texture_file"), True),
        ("roughness", ("reflectionroughness_texture", "roughness_texture", "specular_reflection_roughness_image"), False),
        ("metallic", ("metallic_texture", "metalness_image"), False),
        ("presence", ("opacity_texture",), False),
        ("transmission_color", ("transmission_color_texture", "specular_transmission_color_image"), True),
        ("input_normal", ("normalmap_texture", "normal_map_texture", "normalmap", "normal_image"), False),
    )
    for port, names, is_color in textures:
        if port not in attrs:
            continue
        name = next((n for n in names if isinstance(values.get(n), str) and values[n]), None)
        if name is None:
            continue
        normal = port == "input_normal"
        image = graph.add("ImageNormalMap" if normal else "ImageMap", (-320, len(mapped) * 25))
        image_id = image["id"]
        graph.set_value(image_id, "tangent_space_normal_texture" if normal else "texture", values[name])
        if not normal:
            graph.set_value(image_id, "gamma", 2 if is_color else 0)
        for source_name, target_name in (("texture_scale", "scale"), ("texture_translate", "offset"), ("texture_rotate", "rotation_angle")):
            if source_name in values:
                graph.set_value(image_id, target_name, values[source_name])
                used.add(source_name)
                mapped.append(f"{source_name} → {name}.{target_name}")
        graph.connect(image_id, node_id, port)
        # Graph.connect replaces its graph data, so don't retain mutable node refs.
        if port == "albedo" and destination == "DwaBaseMaterial":
            graph.connect(image_id, node_id, "metallic_color")
        used.add(name)
        mapped.append(f"{name} → {'ImageNormalMap' if normal else 'ImageMap'} → {port}")
        notes.append("Texture maps use UV coordinates. Verify color/data interpretation, channel choice and tiled texture files.")
        if port in attrs and port in graph.node(node_id)["values"]:
            notes.append(f"{port}: a texture replaces the constant; source texture blending needs review.")

    # Empty texture slots and UI-only MDL metadata have no shading effect.
    ignored = {k for k, v in values.items() if v == "" or k.startswith("ui:")}
    unmapped = sorted((set(values) - used - ignored) | set(source.get("connections", [])))
    if source.get("mdl"):
        notes.append("MDL is not evaluated: only authored USD values and simple literal preset defaults are transferred. Base-module defaults and procedural behavior need review.")
    if source.get("animated"):
        notes.append("Animated inputs are sampled at the inspected frame; animation is not translated.")
    if destination != recommended:
        notes.append(f"Destination changed from the recommendation ({recommended}); review the resulting parameter mapping.")
    if source.get("extra_outputs"):
        notes.append("Source displacement/volume outputs are retained for other renderers but are not translated to MoonRay.")
    notes = list(dict.fromkeys(notes))
    approximate = bool(notes or unmapped or source.get("mdl") or destination != recommended)
    graph.validate(require_surface=True)
    return dict(status="Approximation" if approximate else "Mapped", reason=reason,
                mapped=mapped, unmapped=unmapped, notes=notes, graph=graph.data)


def split_arguments(text):
    """Split MDL argument lists without evaluating expressions or loading code."""
    result, start, depth, quoted, escaped = [], 0, 0, False, False
    for i, char in enumerate(text):
        if quoted:
            if char == '"' and not escaped:
                quoted = False
            escaped = char == "\\" and not escaped
            continue
        if char == '"':
            quoted = True
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(text[start:i].strip())
            start = i + 1
    result.append(text[start:].strip())
    return result


def mdl_literal(text):
    """Accept finite scalar/vector literals only, never arbitrary MDL expressions."""
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fd]?", text):
        value = float(text.rstrip("fd"))
        if math.isfinite(value):
            return value
    match = re.fullmatch(r"(color|float[234])\((.*)\)", text, re.S)
    if match:
        count = 3 if match[1] == "color" else int(match[1][-1])
        values = [mdl_literal(arg) for arg in split_arguments(match[2])]
        if all(type(v) in (float, int) for v in values) and len(values) in (1, count):
            return values * count if len(values) == 1 else values
    raise ValueError("Not a supported literal")
