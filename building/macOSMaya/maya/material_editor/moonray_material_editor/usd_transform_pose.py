"""Local/world pose editing without discarding imported USD transform stacks."""
import math

from pxr import Gf, Sdf, Usd, UsdGeom

from .usd_transforms import (CHANNELS, checked_values, euler_from_quaternion, quaternion_from_euler,
                             sample_info, transform_info, transform_ops, write_transform)

MATRIX_NAME = "xformOp:transform:studioMatrix"


def options(space, representation):
    if space not in ("local", "world") or representation not in ("trs", "euler", "matrix"):
        raise ValueError("Choose Local or World space and quaternion TRS, Euler TRS, or Matrix editing.")


def rows(matrix):
    return [[float(matrix[i][j]) for j in range(4)] for i in range(4)]


def checked_matrix(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(not isinstance(row, (list, tuple)) or len(row) != 4 for row in value)
            or any(type(v) not in (int, float) or not math.isfinite(v) for row in value for v in row)):
        raise ValueError("A transform matrix needs 4 rows of 4 finite numbers.")
    if any(abs(value[i][3]) > 1e-12 for i in range(3)) or abs(value[3][3] - 1) > 1e-12:
        raise ValueError("Use an affine USD matrix: the last column must be 0, 0, 0, 1. Translation is in the last row.")
    return Gf.Matrix4d(*[v for row in value for v in row])


def inverse(matrix, label):
    result = matrix.GetInverse()
    if (not all(math.isfinite(v) for row in result for v in row)
            or not Gf.IsClose(matrix * result, Gf.Matrix4d(1), 1e-7)):
        raise ValueError(f"Cannot edit this pose because {label} is singular (for example, zero scale).")
    return result


def compose(values):
    result = Gf.Transform()
    result.SetTranslation(Gf.Vec3d(*values["translate"]))
    q = values["orient"]
    result.SetRotation(Gf.Rotation(Gf.Quatd(q[0], Gf.Vec3d(*q[1:]))))
    result.SetScale(Gf.Vec3d(*values["scale"]))
    return result.GetMatrix()


def decompose(matrix):
    transform = Gf.Transform(matrix)
    q = transform.GetRotation().GetQuat()
    values = dict(translate=list(transform.GetTranslation()), orient=[q.GetReal(), *q.GetImaginary()],
                  scale=list(transform.GetScale()))
    return values, not Gf.IsClose(compose(values), matrix, 1e-7)


def parent_matrix(xform, time):
    return Gf.Matrix4d(1) if xform.GetResetXformStack() else xform.ComputeParentToWorldTransform(time)


def pose_info(prim, frame, mode="auto", space="world", representation="trs", *, include_samples=True):
    options(space, representation)
    # Parent animation also makes world-space fields vary with the timeline.
    parent_animation = False
    if space == "world":
        ancestor = prim
        while ancestor and not ancestor.IsPseudoRoot():
            xform = UsdGeom.Xformable(ancestor)
            if xform and xform.TransformMightBeTimeVarying():
                parent_animation = True
                break
            if xform and xform.GetResetXformStack():
                break
            ancestor = ancestor.GetParent()
    if mode == "auto" and parent_animation:
        mode = "frame"
    info = transform_info(prim, frame, mode, include_samples=include_samples)
    info.update(space=space, representation=representation)
    if "values" not in info:
        return info
    info["animated"] = info["animated"] or parent_animation
    xform = UsdGeom.Xformable(prim)
    time = Usd.TimeCode(frame) if info["time"] == "frame" else Usd.TimeCode.Default()
    local = xform.GetLocalTransformation(time)
    matrix = local * parent_matrix(xform, time) if space == "world" else local
    values, shear = decompose(matrix)
    if space == "local" and not info["adjustment"]:
        # Simple local stacks already have unambiguous component values.
        # Matrix decomposition can move negative scale signs into rotation.
        values = dict(info["values"])
    values["rotateXYZ"] = euler_from_quaternion(values["orient"])
    if space == "local" and not info["adjustment"]:
        _, ops, _ = transform_ops(prim, check_lock=False)
        rotation = ops["orient"]
        if rotation and rotation.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ and rotation.Get(time) is not None:
            # Keep authored turns such as 450 degrees in the local Euler fields.
            values["rotateXYZ"] = list(rotation.Get(time))
    info.update(values=dict(values, matrix=rows(matrix)), has_shear=shear)
    matrix_op = next((op for op in xform.GetOrderedXformOps() if str(op.GetOpName()) == MATRIX_NAME), None)
    info["matrix_authored"] = matrix_op is not None
    if include_samples:
        channel = dict(name=MATRIX_NAME, **sample_info(matrix_op.GetAttr() if matrix_op else None, frame))
        info["channels"]["matrix"] = channel
        if matrix_op or info["adjustment"]:
            for name in CHANNELS:
                info["channels"][name] = dict(channel)
        for op in info["xform_ops"]:
            if op["channel"] == "transform":
                op["channel"] = "matrix"
        info["channels"]["rotateXYZ"] = dict(info["channels"]["orient"])
        if not info["channels"]["orient"]["authored"] and not info["adjustment"] and not matrix_op:
            info["channels"]["rotateXYZ"]["name"] = "xformOp:rotateXYZ"
    return info


def write_local_matrix(prim, target, frame, mode, representation="matrix", key_channels=None):
    target = checked_matrix(rows(target))
    xform, ops, adjustment = transform_ops(prim)
    time = Usd.TimeCode(frame) if mode == "frame" else Usd.TimeCode.Default()
    ordered = xform.GetOrderedXformOps()
    values, shear = decompose(target)
    # Ordinary TRS stacks keep typed component ops and independent sample maps.
    if representation in ("trs", "euler") and not adjustment and not shear:
        old = transform_info(prim, frame, mode, include_samples=False)["values"]
        changed = {name: values[name] for name in CHANNELS
                   if name in (key_channels or ()) or any(abs(a-b) > 1e-9 for a, b in zip(old[name], values[name]))}
        if changed:
            write_transform(prim, changed, frame, mode, "euler" if representation == "euler" else "quaternion")
        return str(prim.GetPath())
    matrix_op = next((op for op in ordered if str(op.GetOpName()) == MATRIX_NAME), None)
    if matrix_op and ordered[0] != matrix_op:
        raise ValueError("The studio matrix has been reordered. Restore it to the start of xformOpOrder before editing the pose.")
    base_ops = [op for op in ordered if op != matrix_op]
    base = UsdGeom.Xformable.GetLocalTransformation(xform, base_ops, time) if base_ops else Gf.Matrix4d(1)
    # USD uses row vectors; the first op in xformOpOrder is applied last.
    correction = inverse(base, "the imported transform stack") * target
    correction = checked_matrix(rows(correction))
    reset = xform.GetResetXformStack()
    original_times = set(xform.GetTimeSamples())
    ancestor = prim
    while ancestor and not ancestor.IsPseudoRoot():
        transform = UsdGeom.Xformable(ancestor)
        if transform:
            original_times.update(transform.GetTimeSamples())
            if transform.GetResetXformStack():
                break
        ancestor = ancestor.GetParent()
    if matrix_op is None:
        matrix_op = xform.AddTransformOp(UsdGeom.XformOp.PrecisionDouble, "studioMatrix")
        matrix_op.Set(Gf.Matrix4d(1))
        if mode == "frame":
            for t in sorted(original_times):
                matrix_op.Set(Gf.Matrix4d(1), t)
    attr = matrix_op.GetAttr()
    if mode == "frame":
        samples = [(t, attr.Get(t)) for t in attr.GetTimeSamples()]
        for t, value in samples:
            attr.Set(Sdf.ValueBlock() if value is None else value, t)
    if not matrix_op.Set(correction, time):
        raise ValueError("USD could not author the transform matrix.")
    xform.SetXformOpOrder([matrix_op, *base_ops], reset)
    return str(prim.GetPath())


def write_pose(prim, values, frame=0, mode="default", space="world", representation="trs"):
    options(space, representation)
    if type(frame) not in (int, float) or not math.isfinite(frame) or mode not in ("default", "frame"):
        raise ValueError("Choose a valid transform frame and editing mode.")
    xform, _, adjustment = transform_ops(prim)
    time = Usd.TimeCode(frame) if mode == "frame" else Usd.TimeCode.Default()
    parent = parent_matrix(xform, time)
    local = xform.GetLocalTransformation(time)
    target = local * parent if space == "world" else local
    if isinstance(values, dict) and set(values) == {"matrix"}:
        target = checked_matrix(values["matrix"])
        representation = "matrix"
        key_channels = None
    else:
        edits = checked_values(values)
        current, shear = decompose(target)
        if shear and set(edits) != {"translate"}:
            raise ValueError("This pose contains shear. Use Matrix editing to preserve it.")
        if space == "local" and representation in ("trs", "euler") and not adjustment and not shear:
            return write_transform(prim, edits, frame, mode, "euler" if representation == "euler" else "quaternion")
        if "rotateXYZ" in edits:
            edits["orient"] = quaternion_from_euler(edits.pop("rotateXYZ"))
        if set(edits) == {"translate"}:
            target.SetTranslateOnly(Gf.Vec3d(*edits["translate"]))
        else:
            current.update(edits)
            target = compose(current)
        key_channels = set(edits)
    if space == "world":
        target = target * inverse(parent, "the parent transform")
    return write_local_matrix(prim, target, frame, mode, representation, key_channels)


def gizmo_axes(world, space):
    axes = [Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1)]
    return [Gf.Transform(world).GetRotation().TransformDir(axis) for axis in axes] if space == "local" else axes


def manipulated_world(world, tool, axis, amount, space):
    """Apply a gesture to a world matrix, keeping rotation/scale at its pivot."""
    result = Gf.Matrix4d(world)
    axes = gizmo_axes(world, space)
    pivot = world.ExtractTranslation()
    if tool == "translate":
        delta = Gf.Vec3d(*amount) if axis < 0 else axes[axis] * amount
        result.SetTranslateOnly(pivot + delta)
        return result
    if tool == "orient":
        delta = Gf.Matrix4d().SetRotate(Gf.Rotation(axes[axis], amount))
    else:
        scale = Gf.Vec3d(*[amount if axis < 0 or i == axis else 1 for i in range(3)])
        delta = Gf.Matrix4d().SetScale(scale)
        if space == "local":
            rotation = Gf.Matrix4d().SetRotate(Gf.Transform(world).GetRotation())
            delta = rotation.GetTranspose() * delta * rotation
    return world * Gf.Matrix4d().SetTranslate(-pivot) * delta * Gf.Matrix4d().SetTranslate(pivot)
