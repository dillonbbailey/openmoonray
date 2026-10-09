"""Composition-preserving final-render preparation and native EXR publication."""
import json
import os
from pathlib import Path
import subprocess

from .ovrtx_config import PREFIX as VIEWER_PREFIX, require_runtime, runtime_environment


def prepare(data, directory):
    from pxr import CameraUtil, Gf, Usd, UsdGeom
    from .ovrtx_scene import matrix_values, write_snapshot
    stage = Usd.Stage.Open(data["input"])
    if not stage:
        raise ValueError("Could not open the USD scene for ovRTX.")
    snapshot = write_snapshot(stage, directory / "ovrtx-scene", purposes=data["purpose"],
                              freeze=True, default_light=data["use_default_light"])
    copy = Usd.Stage.Open(snapshot["scene"])
    time = Usd.TimeCode(data["frame"])
    camera_path = data["camera"]
    if camera_path:
        schema = UsdGeom.Camera(stage.GetPrimAtPath(camera_path))
        if not schema:
            raise ValueError("The camera path does not identify a USD camera: " + camera_path)
        camera = schema.GetCamera(time)
    else:
        # An unframed file has no viewport navigation camera. Fit its bounds.
        bounds = UsdGeom.BBoxCache(time, ["default", *data["purpose"]]).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
        center = bounds.GetMidpoint() if not bounds.IsEmpty() else Gf.Vec3d(0)
        radius = max(bounds.GetSize().GetLength() * .5, .1) if not bounds.IsEmpty() else 1
        z_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
        up = Gf.Vec3d(0, 0, 1) if z_up else Gf.Vec3d(0, 1, 0)
        direction = Gf.Vec3d(1, -2, 1) if z_up else Gf.Vec3d(1, 1, 2)
        aspect = data["width"] / data["height"]
        distance = radius * 6 * max(1, 1 / aspect)
        camera = Gf.Camera()
        camera.transform = Gf.Matrix4d().SetLookAt(center + direction.GetNormalized() * distance, center, up).GetInverse()
        camera.clippingRange = Gf.Range1f(max(.001, distance / 10000), max(1000, distance * 10))
        camera.focusDistance = distance
        camera.fStop = 0
    CameraUtil.ConformWindow(camera, CameraUtil.MatchHorizontally, data["width"] / data["height"])
    if data["force_polygon"]:
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Mesh):
                UsdGeom.Mesh(copy.OverridePrim(prim.GetPath())).CreateSubdivisionSchemeAttr("none")
        copy.GetRootLayer().Save()
    packet = dict(transform=matrix_values(camera.transform),
        projection="orthographic" if camera.projection == Gf.Camera.Orthographic else "perspective",
        focalLength=float(camera.focalLength), horizontalAperture=float(camera.horizontalAperture),
        verticalAperture=float(camera.verticalAperture), horizontalApertureOffset=float(camera.horizontalApertureOffset),
        verticalApertureOffset=float(camera.verticalApertureOffset), focusDistance=float(camera.focusDistance),
        fStop=float(camera.fStop), clippingRange=[camera.clippingRange.min, camera.clippingRange.max])
    # ovstage's time_code argument is expressed in seconds, unlike Usd.TimeCode.
    return dict(snapshot, camera=packet, time=data["frame"] / stage.GetTimeCodesPerSecond(),
                size=[data["width"], data["height"]],
                output_name="HdrColor", output_names=data.get("ovrtx_outputs", ["HdrColor"]), settings=data["ovrtx_settings"],
                live_updates=data["live_updates"], update_interval=data["update_interval"],
                progress_step=data["progress_step"])


def write_exr(source, destination, width, height):
    import numpy as np
    import OpenImageIO as oiio
    from .ovrtx_outputs import channel_names, validate_outputs
    loaded = np.load(source, allow_pickle=False)
    if isinstance(loaded, np.ndarray):
        images = {"HdrColor": loaded}
    else:
        try:
            images = {name: loaded[name] for name in validate_outputs(loaded.files)}
        finally:
            loaded.close()
    arrays, channels = [], []
    for name, image in images.items():
        names = channel_names(name)
        if image.shape != (height, width, len(names)) or image.dtype not in (np.float16, np.float32, np.uint8):
            raise ValueError("Invalid ovRTX image: " + name)
        values = image.astype(np.float32)
        if image.dtype == np.uint8:
            values /= 255
        arrays.append(values)
        channels.extend(names)
    pixels = np.concatenate(arrays, axis=2)
    temporary = destination.with_name(destination.stem + ".tmp.exr")
    spec = oiio.ImageSpec(width, height, len(channels), oiio.FLOAT)
    spec.channelnames = channels
    spec.attribute("oiio:ColorSpace", "Linear")
    spec.attribute("Software", "MoonLab ovRTX")
    spec.attribute("moonlab:ovrtx:outputs", json.dumps(list(images)))
    output = oiio.ImageOutput.create(str(temporary))
    if not output or not output.open(str(temporary), spec):
        raise ValueError("Could not create ovRTX EXR output.")
    try:
        if not output.write_image(pixels):
            raise ValueError(output.geterror() or "Could not write ovRTX EXR pixels.")
    except BaseException:
        output.close()
        raise
    if not output.close():
        raise ValueError(output.geterror() or "Could not finish ovRTX EXR output.")
    os.replace(temporary, destination)


def render(data, directory):
    from .exr_image import display_exr, inspect_exr
    from .render_region import merge_region
    from .ovrtx_final_worker import PREFIX
    from .ovrtx_outputs import apply_display_hints
    python = require_runtime()
    request = directory / "ovrtx-request.json"
    print(f"ovRTX: Preparing USD snapshot · frame {data['frame']:g} · {data['width']} × {data['height']}\nRender progress: busy", flush=True)
    request.write_text(json.dumps(prepare(data, directory)))
    print("ovRTX: Outputs · " + ", ".join(data.get("ovrtx_outputs", ["HdrColor"])), flush=True)
    print("ovRTX: Starting renderer…", flush=True)
    process = subprocess.Popen([python, "-m", "moonray_editor.ovrtx_final_worker", str(request)],
                               env=runtime_environment(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    complete = False
    seen_lines = set()
    try:
        for line in process.stdout:
            if line.startswith(VIEWER_PREFIX):
                print(json.loads(line[len(VIEWER_PREFIX):]).get("message", ""), flush=True)
                continue
            if not line.startswith(PREFIX):
                seen_lines.add(line.strip())
                print(line, end="", flush=True)
                continue
            event = json.loads(line[len(PREFIX):])
            output = directory / ("render.exr" if event["final"] else "live.exr")
            write_exr(event["file"], output, data["width"], data["height"])
            print("ovRTX: " + ("Final EXR ready" if event["final"] else "Live EXR updated"), flush=True)
            Path(event["file"]).unlink()
            complete |= event["final"]
        if process.wait() != 0 or not complete:
            raise ValueError("ovRTX rendering failed. See the renderer output above.")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        log = directory / "ovrtx.log"
        if log.exists():
            for line in log.read_text(errors="replace").splitlines():
                if line.strip() not in seen_lines:
                    print(line, flush=True)
    output = directory / "render.exr"
    merge_region(output, data.get("region_base"), data["render_region"], data["width"], data["height"])
    result = apply_display_hints(inspect_exr(output))
    result.update(render_region=data["render_region"], region_display=data.get("region_display"))
    if not data["background"]:
        result["image"] = str(directory / "display.png")
        result["display_info"] = display_exr(output, result["image"], selection={}, metadata=result)
    print("Render progress: 100%", flush=True)
    return result
