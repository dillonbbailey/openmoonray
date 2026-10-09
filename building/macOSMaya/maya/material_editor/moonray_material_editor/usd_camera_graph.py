"""Capture a USD camera at the current frame for MoonRay projection shaders."""
from pxr import Sdf, Tf, Usd, UsdGeom


def camera_info(prim, scene, frame=0):
    if not prim or not prim.IsActive() or not prim.IsA(UsdGeom.Camera):
        raise ValueError("Choose a USD camera.")
    camera = UsdGeom.Camera(prim)
    time = Usd.TimeCode(frame)
    cam = camera.GetCamera(time)
    ortho = camera.GetProjectionAttr().Get(time) == UsdGeom.Tokens.orthographic
    # USD apertures are tenths of a scene unit. Perspective cameras keep the
    # same focal/aperture ratio; orthographic MoonRay apertures use scene units.
    factor = .1 if ortho else 1.
    values = dict(node_xform=[list(row) for row in camera.ComputeLocalToWorldTransform(time)],
                  film_width_aperture=cam.horizontalAperture * factor,
                  horizontal_film_offset=cam.horizontalApertureOffset * factor,
                  vertical_film_offset=cam.verticalApertureOffset * factor,
                  near=cam.clippingRange.GetMin(), far=cam.clippingRange.GetMax(),
                  dof=cam.fStop > 0, dof_aperture=cam.fStop if cam.fStop > 0 else 8.,
                  dof_focus_distance=cam.focusDistance)
    if not ortho:
        values["focal"] = cam.focalLength
    return dict(path=str(prim.GetPath()), name=prim.GetDisplayName() or prim.GetName(),
                shader="OrthographicCamera" if ortho else "PerspectiveCamera", values=values,
                reference=dict(scene=scene, path=str(prim.GetPath()), frame=frame),
                aspect=cam.horizontalAperture / cam.verticalAperture if cam.verticalAperture else 1.)


class CameraWatch:
    """Track graph-linked scene prims; never author USD inside a notice."""
    def __init__(self, info=camera_info):
        self.info = info
        self.paths = set()
        self.dirty = set()
        self.last = {}

    def watch(self, paths):
        paths = set(paths)
        self.dirty = (self.dirty | (paths - self.paths)) & paths
        self.paths = paths
        self.last = {path: value for path, value in self.last.items() if path in paths}

    def changed(self, notice):
        resync = notice.GetResyncedPaths()
        changed = notice.GetChangedInfoOnlyPaths()
        for name in self.paths:
            path = Sdf.Path(name)
            if any(path.HasPrefix(p.GetPrimPath()) for p in resync) or any(
                    path == p.GetPrimPath() or path.HasPrefix(p.GetPrimPath()) and (
                        p.IsPrimPath() or p == Sdf.Path.absoluteRootPath
                        or p.name == "xformOpOrder" or p.name.startswith("xformOp:"))
                    for p in changed):
                self.dirty.add(name)

    def sample(self, stage, scene, frame, force=False):
        result = []
        for path in sorted(self.paths if force else self.dirty):
            try:
                info = self.info(stage.GetPrimAtPath(path), scene, frame)
                # Frame changes alone must not trigger shader writes or renders.
                value = (info.get("shader"), info["values"])
            except (ValueError, Tf.ErrorException) as exc:
                info = dict(path=path, error=str(exc))
                value = info
            if force or self.last.get(path) != value:
                result.append(info)
            self.last[path] = value
        self.dirty.clear()
        return result
