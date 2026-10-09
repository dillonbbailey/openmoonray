"""Persistent ovRTX process. Only this optional environment imports RTX libraries."""
import json
import os
from pathlib import Path
import sys

from .ovrtx_config import PREFIX
from .ovrtx_settings import render_attributes, validate_settings


def emit(event, **data):
    print(PREFIX + json.dumps(dict(event=event, **data)), flush=True)


class Renderer:
    def __init__(self, directory):
        import numpy as np
        import ovrtx
        import ovstage
        self.np, self.ovrtx, self.ovstage = np, ovrtx, ovstage
        self.directory = Path(directory)
        self.renderer = ovrtx.Renderer(ovrtx.RendererConfig(
            log_file_path=str(self.directory / "ovrtx.log"), log_level="warning"))
        self.stage = ovstage.Stage("moonlab.viewport")
        self.renderer.attach_ovstage(self.stage)
        self.ordinal = 0
        self.scene = None
        self.camera = None
        self.time = None
        self.size = None
        self.settings = None

    def write(self, path, name, values, dtype="float32", lanes=1, semantic=None):
        ovstage, np = self.ovstage, self.np
        values = np.asarray(values, dtype=dtype)
        with ovstage.PathDictionary(self.stage) as paths:
            path_list = paths.create_path_list_from_strings([path])
            try:
                with self.stage.query_from_path_list(path_list) as query:
                    if query.result().total_prim_count != 1:
                        raise ValueError("ovRTX prim is unavailable: " + path)
                    token = paths.intern_token(name)
                    tensor = ovstage.make_dltensor(values, dtype=ovstage.numpy_to_dldatatype(values.dtype, lanes=lanes), shape=[1], ndim=1)
                    kwargs = dict(ordinal=self.ordinal, tensors=tensor, is_array=False)
                    if semantic is not None:
                        kwargs["semantic"] = semantic
                    self.stage.write_attribute(query, token, **kwargs).wait()
            finally:
                paths.destroy_path_list(path_list)

    def matrix(self, path, value):
        self.write(path, "omni:xform", value, "float64", 16, self.ovstage.AttributeSemantic.MATRIX)

    def load(self, data):
        scene, ns = data["scene"], data["namespace"]
        self.camera_path, self.product = ns + "/Camera", ns + "/Product"
        output_name = data.get("output_name", "LdrColor")
        if output_name not in ("LdrColor", "HdrColor"):
            raise ValueError("Unsupported ovRTX color output.")
        self.output = self.product + "/" + output_name
        from .ovrtx_outputs import OUTPUTS
        names = data.get("output_names", [output_name])
        if not names or any(name not in OUTPUTS for name in names):
            raise ValueError("Unsupported ovRTX render output.")
        self.outputs = {name: self.product + "/" + name for name in names}
        ordered_vars = ", ".join("<" + path + ">" for path in self.outputs.values())
        definitions = "\n".join(f'def RenderVar "{name}" {{\n    string sourceName = "{name}"\n}}' for name in names)
        w, h = data["size"]
        # JSON string quoting is also valid for these ordinary USD token strings;
        # layer asset paths are authored by the native USD worker.
        text = f'''#usda 1.0
(
    subLayers = [@@@{scene}@@@]
    metersPerUnit = {data.get('meters_per_unit', 0.01)}
    upAxis = {json.dumps(data.get('up_axis', 'Y'))}
    timeCodesPerSecond = {data.get('time_codes_per_second', 24)}
)
def Scope "{ns[1:]}" {{
    def Camera "Camera" {{
        matrix4d xformOp:transform = ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1))
        uniform token[] xformOpOrder = ["xformOp:transform"]
        token projection = {json.dumps(data['camera']['projection'])}
        float focalLength = 50
        float horizontalAperture = 20.955
        float verticalAperture = 15.2908
        float horizontalApertureOffset = 0
        float verticalApertureOffset = 0
        float2 clippingRange = (0.1, 1000000)
        float focusDistance = 5
        float fStop = 0
    }}
    def RenderProduct "Product" {{
        int2 resolution = ({w}, {h})
        rel camera = <{self.camera_path}>
        rel orderedVars = [{ordered_vars}]
        token omni:rtx:rendermode = "RealTimePathTracing"
        {definitions}
    }}
}}
'''
        self.ovstage.population.open_usd_from_string(self.stage, text, ordinal=self.ordinal, time_code=data["time"])
        self.scene, self.size, self.time, self.camera = scene, [w, h], data["time"], None
        self.settings = None

    def apply_settings(self, values):
        previous = render_attributes(self.settings) if self.settings is not None else {}
        attributes = render_attributes(values)
        changed = False
        for name, (value, dtype) in attributes.items():
            if previous.get(name) == (value, dtype):
                continue
            if dtype == "token":
                with self.ovstage.PathDictionary(self.stage) as paths:
                    token = paths.intern_token(value)
                self.write(self.product, name, [token], "uint64",
                           semantic=self.ovstage.AttributeSemantic.TOKEN_ID)
            else:
                self.write(self.product, name, [value], dtype)
            changed = True
        self.settings = values
        return changed

    def render_frame(self, data, *, progress=None, all_outputs=False):
        settings = validate_settings(data.get("settings", {}))
        self.ordinal += 1
        camera = data["camera"]
        reload = self.scene != data["scene"] or (self.camera and camera["projection"] != self.camera["projection"])
        if reload:
            emit("status", message="Loading ovRTX scene…")
            self.load(data)
        elif data["time"] != self.time:
            self.ovstage.population.update_from_usd_time(self.stage, ordinal=self.ordinal, time_code=data["time"])
            self.time = data["time"]
            self.camera = None
        if data["size"] != self.size:
            self.write(self.product, "resolution", data["size"], "int32", 2)
            self.size = data["size"]
        for path, matrix in data.get("transforms", {}).items():
            self.matrix(path, matrix)
        if camera != self.camera:
            self.matrix(self.camera_path, camera["transform"])
            for name in ("focalLength", "horizontalAperture", "verticalAperture", "horizontalApertureOffset",
                         "verticalApertureOffset", "focusDistance", "fStop"):
                self.write(self.camera_path, name, [camera[name]])
            self.write(self.camera_path, "clippingRange", camera["clippingRange"], lanes=2)
            self.camera = camera
        settings_changed = self.apply_settings(settings)
        self.stage.advance_write_floor(self.ordinal, self.ovstage.Scope.ALL).wait()
        if settings_changed:
            self.renderer.reset()
        if progress is None:
            products = self.renderer.step(render_products={self.product}, delta_time=1.0 / 60, ordinal=self.ordinal)
        else:
            operation = self.renderer.step_async(render_products={self.product}, delta_time=1.0 / 60, ordinal=self.ordinal)
            pending = operation.wait(timeout_ns=250_000_000)
            while pending is None:
                progress(operation.query_status())
                pending = operation.wait(timeout_ns=250_000_000)
            products = pending.fetch()
        product = products[self.product]
        frame = product.frames[-1]
        pixels = {}
        for name, path in (self.outputs if all_outputs else {"color": self.output}).items():
            mapping = frame.render_vars[path].map(device=self.ovrtx.Device.CPU)
            try:
                view = self.np.from_dlpack(mapping)
                pixels[name] = view.copy()
                del view
            finally:
                mapping.unmap()
        return (pixels if all_outputs else pixels["color"]), bool(reload)

    def render(self, data):
        pixels, reloaded = self.render_frame(data, all_outputs=True)
        height, width, channels = pixels["LdrColor"].shape
        if pixels["LdrColor"].dtype != self.np.uint8 or channels != 4:
            raise RuntimeError("Unexpected ovRTX color output format.")
        temporary = self.directory / "frame.tmp"
        with temporary.open("wb") as stream:
            self.np.savez(stream, **pixels)
        destination = self.directory / "frame.npz"
        os.replace(temporary, destination)
        emit("frame", request=data["request"], width=width, height=height, file=str(destination), reloaded=reloaded)

    def close(self):
        self.renderer.detach_ovstage()
        self.stage.destroy()
        self.renderer.destroy()


def main():
    # Linux workers die with their owning USD process, including after a crash.
    if sys.platform.startswith("linux"):
        import ctypes
        import signal
        parent = os.getppid()
        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() != parent:
            return 1
    renderer = None
    try:
        emit("status", message="Starting ovRTX; the first launch may compile shaders…")
        renderer = Renderer(sys.argv[1])
        emit("ready")
        for line in sys.stdin:
            data = json.loads(line)
            if data.get("command") == "shutdown":
                break
            renderer.render(data)
    except Exception as exc:
        emit("error", message=str(exc))
        return 1
    finally:
        if renderer is not None:
            renderer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
