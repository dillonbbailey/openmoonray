# Copyright 2023-2024 DreamWorks Animation LLC
# SPDX-License-Identifier: Apache-2.0
"""The MoonRay render settings UI.

mayaHydra makes a defaultRenderGlobals attribute per render setting the
delegate declares ("HdMoonrayRendererPlugin__<key>") and a generated, flat
option box labelled with the raw keys. This lays the same attributes out in
sections with readable labels, tooltips and menus for enums, and is used for:
  - the MoonRay tab of Render Settings (Render Using: Hydra Moonray)
  - the option box next to "Hydra Moonray" in the viewport Renderer menu
  - the Hydra Settings section of defaultRenderGlobals in the Attribute Editor
Edits are pushed to the running viewport like mayaHydra's own controls do.
"""
from maya import cmds, mel

from . import RENDERER

NODE = "defaultRenderGlobals"

# (key, label, tooltip[, choices]); choices: [(value, label)] for menus.
SECTIONS = [
    ("Sampling", False, [
        ("samplingMode", "Sampling", "Uniform: the same number of samples everywhere. Adaptive: more samples where the image is noisy.",
         [(0, "Uniform"), (2, "Adaptive")]),
        ("pixelSamples", "Pixel Samples", "Samples per pixel in uniform mode, squared: 8 renders 64 samples."),
        ("minAdaptiveSamples", "Min Adaptive Samples", "Adaptive mode: samples every pixel gets before noise is measured."),
        ("maxAdaptiveSamples", "Max Adaptive Samples", "Adaptive mode: most samples a pixel can get."),
        ("targetAdaptiveError", "Target Adaptive Error", "Adaptive mode: lower is cleaner and slower."),
    ]),
    ("Clamping", False, [
        ("sampleClampingValue", "Clamp Value", "Clamp sample values to this maximum (0 turns clamping off). Removes fireflies; biased."),
        ("sampleClampingDepth", "Clamp After Depth", "Clamp only samples past this many non-specular bounces."),
    ]),
    ("Ray Depth", False, [
        ("maxDepth", "Total", "Maximum ray depth over all bounce types."),
        ("maxDiffuseDepth", "Diffuse", "Maximum diffuse bounces."),
        ("maxGlossyDepth", "Glossy", "Maximum glossy bounces."),
        ("maxMirrorDepth", "Mirror / Refraction", "Maximum mirror and refraction bounces."),
        ("maxVolumeDepth", "Volume", "Maximum volume scattering bounces."),
        ("maxHairDepth", "Hair", "Maximum hair bounces."),
        ("maxPresenceDepth", "Presence", "Maximum cut-out (presence) layers a ray passes through."),
    ]),
    ("Scene", False, [
        ("enableMotionBlur", "Motion Blur", "Render motion blur."),
        ("enablePresenceShadows", "Presence Shadows", "Cut-out (presence) geometry casts partial shadows."),
        ("textureCacheSize", "Texture Cache (MB)", "Texture memory MoonRay keeps loaded."),
        ("doubleSided", "Force Double-Sided", "Render all geometry double-sided."),
        ("forcePolygon", "Force Polygons", "Render subdivision surfaces as polygons."),
        ("disableLighting", "Disable Lighting", "Ignore scene lights (debugging)."),
        ("decodeNormals", "Decode Normals", "Decode normals stored as colors."),
    ]),
    ("Denoising", False, [
        ("enableDenoise", "Denoise", "Denoise the image."),
        ("enableOIDN", "Use OIDN", "Use Intel Open Image Denoise (otherwise OptiX)."),
        ("denoiseAlbedoGuiding", "Albedo Guiding", "Use the albedo pass to guide the denoiser."),
        ("denoiseNormalGuiding", "Normal Guiding", "Use the normal pass to guide the denoiser."),
    ]),
    ("Renderer", True, [
        ("executionMode", "Execution Mode", "auto picks vectorized (CPU) or xpu (GPU) where available.",
         [("auto", "Auto"), ("vectorized", "Vectorized (CPU)"), ("scalar", "Scalar (CPU)"), ("xpu", "XPU (GPU)")]),
        ("maxFps", "Viewport Max FPS", "Frame rate the viewport asks MoonRay for."),
        ("localReservedCores", "Reserved Cores", "CPU cores left free for Maya."),
        ("useRemoteHosts", "Use Remote Hosts", "Render on remote Arras hosts."),
        ("remoteHosts", "Remote Hosts", "Number of remote hosts."),
        ("maxConnectRetries", "Connect Retries", "Attempts to connect to the renderer."),
    ]),
    ("Debug", True, [
        ("logLevel", "Log Level", "Renderer log level."),
        ("info", "Info Messages", "Print MoonRay info messages."),
        ("debug", "Debug Messages", "Print MoonRay debug messages."),
        ("rdlOutput", "RDL Output", "Write the scene MoonRay receives to this .rdla file."),
        ("pruneVolume", "Prune Volumes", "Skip volumes."),
        ("pruneWillow", "Prune Willow", "Skip WillowGeometry procedurals."),
        ("pruneFurDeform", "Prune FurDeform", "Skip FurDeformGeometry procedurals."),
        ("pruneWrapDeform", "Prune WrapDeform", "Skip WrapDeformGeometry procedurals."),
        ("pruneCurveDeform", "Prune CurveDeform", "Skip CurveDeformGeometry procedurals."),
    ]),
]

ADAPTIVE_ONLY = ("minAdaptiveSamples", "maxAdaptiveSamples", "targetAdaptiveError")
UNIFORM_ONLY = ("pixelSamples",)

_controls = {}  # key -> control name, for the current layout


def attr(key):
    return f"{RENDERER}__{key}"


def plug(key):
    return f"{NODE}.{attr(key)}"


def ensure_globals():
    """mayaHydra creates the attributes on demand."""
    if not cmds.attributeQuery(attr("pixelSamples"), node=NODE, exists=True):
        cmds.mayaHydra(createRenderGlobals=True, renderer=RENDERER, userDefaults=True)


def apply_command(key):
    # mayaHydra's own control change command: pushes the value to the viewport.
    return f"mtohRenderOverride_ApplySetting {RENDERER} {attr(key)} {NODE}"


def changed(key):
    mel.eval(apply_command(key))
    update()


def _menu(key, label, tip, choices):
    current = cmds.getAttr(plug(key))
    menu = cmds.optionMenuGrp(label=label, annotation=tip, columnWidth2=(160, 180),
                              changeCommand=lambda value, k=key, c=choices: _menu_changed(k, c, value))
    for _, text in choices:
        cmds.menuItem(label=text)
    labels = [text for value, text in choices]
    values = [value for value, text in choices]
    if current in values:
        cmds.optionMenuGrp(menu, edit=True, value=labels[values.index(current)])
    return menu


def _menu_changed(key, choices, text):
    value = dict((label, value) for value, label in choices)[text]
    if isinstance(value, str):
        cmds.setAttr(plug(key), value, type="string")
    else:
        cmds.setAttr(plug(key), value)
    changed(key)


def build():
    """Build the sections under the current parent layout."""
    ensure_globals()
    _controls.clear()
    column = cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
    for title, collapsed, rows in SECTIONS:
        rows = [row for row in rows if cmds.attributeQuery(attr(row[0]), node=NODE, exists=True)]
        if not rows:
            continue
        cmds.frameLayout(label=title, collapsable=True, collapse=collapsed, marginWidth=6, marginHeight=4)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=2)
        for key, label, tip, *choices in rows:
            if choices:
                _controls[key] = _menu(key, label, tip, choices[0])
            else:
                _controls[key] = cmds.attrControlGrp(attribute=plug(key), label=label, annotation=tip,
                                                     changeCommand=lambda k=key: changed(k))
        cmds.setParent("..")
        cmds.setParent("..")
    cmds.setParent("..")
    update()
    return column


def update():
    """Enable the sample controls that apply to the sampling mode."""
    if not _controls or not cmds.attributeQuery(attr("samplingMode"), node=NODE, exists=True):
        return
    adaptive = cmds.getAttr(plug("samplingMode")) != 0
    for key in ADAPTIVE_ONLY + UNIFORM_ONLY:
        control = _controls.get(key)
        if control and cmds.control(control, exists=True):
            cmds.control(control, edit=True, enable=adaptive == (key in ADAPTIVE_ONLY))


def build_editor_template():
    """Attribute Editor (defaultRenderGlobals > Hydra Settings) version."""
    for title, collapsed, rows in SECTIONS:
        cmds.editorTemplate(beginLayout=title, collapse=collapsed)
        for key, label, tip, *_ in rows:
            if cmds.attributeQuery(attr(key), node=NODE, exists=True):
                cmds.editorTemplate(attr(key), label=label, annotation=tip,
                                    addDynamicControl=True)
        cmds.editorTemplate(endLayout=True)


# ----- Render Settings window tab -------------------------------------------

def create_tab():
    """addGlobalsTab create procedure: called with the tab's layout as parent."""
    parent = cmds.setParent(query=True)  # a formLayout made by Render Settings
    cmds.setUITemplate("renderGlobalsTemplate", pushTemplate=True)
    cmds.setUITemplate("attributeEditorTemplate", pushTemplate=True)
    scroll = cmds.scrollLayout(horizontalScrollBarThickness=0, childResizable=True)
    build()
    cmds.setParent(parent)
    # Fill the tab, as Maya's own tabs do; unattached it gets no size.
    cmds.formLayout(parent, edit=True, attachForm=[(scroll, "top", 0), (scroll, "bottom", 0),
                                                   (scroll, "left", 0), (scroll, "right", 0)])
    cmds.setUITemplate(popTemplate=True)
    cmds.setUITemplate(popTemplate=True)


def update_tab():
    update()


# ----- viewport option box ---------------------------------------------------

def build_options(from_ae):
    if from_ae:
        build_editor_template()
    else:
        build()
