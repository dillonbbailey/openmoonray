"""Non-destructive TRS editing for the renderer's USD runtime."""
import math

from pxr import Gf, Sdf, Usd, UsdGeom

from .usd_editing import editable_prim, encode_value
from .usd_transform_lock import lock_info, require_unlocked

CHANNELS = ("translate", "orient", "scale")
DEFAULTS = dict(translate=[0, 0, 0], orient=[1, 0, 0, 0], scale=[1, 1, 1])
TYPES = dict(translate=UsdGeom.XformOp.TypeTranslate, orient=UsdGeom.XformOp.TypeOrient,
             scale=UsdGeom.XformOp.TypeScale)
SUFFIX = "studio"


def quaternion_from_euler(angles):
    rotation = (Gf.Rotation(Gf.Vec3d(1, 0, 0), angles[0]) *
                Gf.Rotation(Gf.Vec3d(0, 1, 0), angles[1]) *
                Gf.Rotation(Gf.Vec3d(0, 0, 1), angles[2]))
    q = rotation.GetQuat()
    return [q.GetReal(), *q.GetImaginary()]


def euler_from_quaternion(parts):
    rotation = Gf.Rotation(Gf.Quatd(parts[0], Gf.Vec3d(*parts[1:])))
    # USD rotateXYZ applies X, then Y, then Z (row-vector convention).
    return list(reversed(rotation.Decompose(Gf.Vec3d(0, 0, 1), Gf.Vec3d(0, 1, 0), Gf.Vec3d(1, 0, 0))))


def nearest_euler(angles, reference):
    return [angle + 360 * round((previous - angle) / 360) for angle, previous in zip(angles, reference)]


def transform_ops(prim, *, check_lock=True):
    if check_lock:
        require_unlocked(prim)
    if not editable_prim(prim):
        raise ValueError("Select an editable transformable prim. Instance contents are read-only.")
    xform = UsdGeom.Xformable(prim)
    if not xform:
        raise ValueError("This prim has no transform. Select its geometry or Xform parent.")
    ordered = xform.GetOrderedXformOps()
    names = [str(op.GetOpName()) for op in ordered]
    rotation = "rotateXYZ" if "xformOp:rotateXYZ" in names else "orient"
    standard = ["xformOp:" + name for name in ("translate", rotation, "scale")]
    direct = names == [name for name in standard if name in names]
    if direct:
        return xform, {name: next((op for op in ordered if str(op.GetOpName()) == full), None)
                       for name, full in zip(CHANNELS, standard)}, False
    # Preserve arbitrary matrices, Euler rotations, pivots, and animation. A
    # separate local adjustment is evaluated before the imported transform stack.
    rotation = "rotateXYZ" if "xformOp:rotateXYZ:" + SUFFIX in names else "orient"
    adjustment = ["xformOp:" + name + ":" + SUFFIX for name in ("translate", rotation, "scale")]
    existing = [name for name in names if name in adjustment]
    if existing and names[-len(existing):] != [name for name in adjustment if name in existing]:
        raise ValueError("The studio adjustment ops have been reordered; edit the individual properties instead.")
    return xform, {name: next((op for op in ordered if str(op.GetOpName()) == full), None)
                   for name, full in zip(CHANNELS, adjustment)}, True


def sample_info(attr, frame):
    brackets = attr.GetBracketingTimeSamples(frame) if attr else None
    return dict(samples=attr.GetNumTimeSamples() if attr else 0,
                has_sample=bool(brackets and frame in brackets), authored=bool(attr))


def transform_info(prim, frame, mode="auto", *, include_samples=True):
    result = dict(path=str(prim.GetPath()) if prim else "", frame=frame, editable=False, **lock_info(prim))
    try:
        xform, ops, adjustment = transform_ops(prim, check_lock=False)
        animated = xform.TransformMightBeTimeVarying()
        mode = ("frame" if animated else "default") if mode == "auto" else mode
        time = Usd.TimeCode(frame) if mode == "frame" else Usd.TimeCode.Default()
        values = {}
        for name, op in ops.items():
            value = op.Get(time) if op else None
            values[name] = encode_value(value) if value is not None else DEFAULTS[name]
            if name == "orient" and op and op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ and value is not None:
                values[name] = quaternion_from_euler(value)
        result.update(editable=not result["transforms_locked"], values=values, time=mode, animated=animated, adjustment=adjustment,
                      types={name: str(op.GetAttr().GetTypeName()) if op else ("quatf" if name == "orient" else "float3")
                             for name, op in ops.items()})
        if include_samples:
            result["channels"] = {}
            for name, op in ops.items():
                attribute = str(op.GetAttr().GetName()) if op else "xformOp:" + name + (":" + SUFFIX if adjustment else "")
                result["channels"][name] = dict(name=attribute, **sample_info(op.GetAttr() if op else None, frame))
            attributes = {str(op.GetAttr().GetName()): op.GetAttr() for op in xform.GetOrderedXformOps()}
            result["xform_ops"] = []
            for name, attr in attributes.items():
                operation = name.split(":")[1]
                channel = "orient" if operation.startswith("rotate") or operation == "orient" else operation
                result["xform_ops"].append(dict(name=name, channel=channel, type=str(attr.GetTypeName()),
                                                **sample_info(attr, frame)))
    except ValueError as exc:
        result["reason"] = str(exc)
    if result["transforms_locked"]:
        result["reason"] = result["transform_lock_reason"]
    return result


def checked_values(values):
    if not isinstance(values, dict) or not values or set(values) - {*CHANNELS, "rotateXYZ"}:
        raise ValueError("Choose translation, quaternion orientation, Euler XYZ rotation, or scale.")
    if "orient" in values and "rotateXYZ" in values:
        raise ValueError("Choose either Euler XYZ or quaternion rotation, not both.")
    result = {}
    for name, parts in values.items():
        if (not isinstance(parts, (list, tuple)) or len(parts) != (4 if name == "orient" else 3)
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in parts)):
            raise ValueError("Transform components must be finite floating-point numbers.")
        parts = list(parts)
        if name == "orient":
            length = math.hypot(*parts)
            if length < 1e-12:
                raise ValueError("A rotation quaternion must have a nonzero length.")
            parts = [v / length for v in parts]
        result[name] = parts
    return result


def rotation_op(xform, current, adjustment, euler):
    """Replace the ordered rotation, retaining defaults and authored key poses."""
    kind = UsdGeom.XformOp.TypeRotateXYZ if euler else UsdGeom.XformOp.TypeOrient
    if current and current.GetOpType() == kind:
        return current
    default = current.Get() if current else None
    samples = [(t, current.Get(t)) for t in current.GetTimeSamples()] if current else []
    precision = current.GetPrecision() if current else UsdGeom.XformOp.PrecisionFloat
    name = "xformOp:" + ("rotateXYZ" if euler else "orient") + (":" + SUFFIX if adjustment else "")
    # Switching back can reuse an inactive operation with its original precision.
    existing = xform.GetPrim().GetAttribute(name)
    if existing:
        precision = UsdGeom.XformOp(existing).GetPrecision()
    result = xform.AddXformOp(kind, precision, SUFFIX if adjustment else "")
    attr = result.GetAttr()
    attr.Block()  # Remove old inactive samples, including weaker opinions.
    previous = None
    def converted(value):
        nonlocal previous
        if value is None:
            return Sdf.ValueBlock()
        parts = euler_from_quaternion(encode_value(value)) if euler else quaternion_from_euler(value)
        if euler:
            if previous is not None:
                parts = nearest_euler(parts, previous)
            previous = parts
        cls = type(attr.GetTypeName().defaultValue)
        return cls(*parts) if euler else cls(parts[0], type(attr.GetTypeName().defaultValue.GetImaginary())(*parts[1:]))
    attr.Set(converted(default) if default is not None else (Gf.Vec3f(0) if euler else Gf.Quatf(1)))
    for t, value in samples:
        attr.Set(converted(value), t)
    return result


def write_transform(prim, values, frame=0, mode="default", rotation_format=None):
    values = checked_values(values)
    if type(frame) not in (int, float) or not math.isfinite(frame) or mode not in ("default", "frame"):
        raise ValueError("Choose a valid transform frame and editing mode.")
    xform, ops, adjustment = transform_ops(prim)
    ordered = xform.GetOrderedXformOps()
    base = [op for op in ordered if op not in list(ops.values())]
    reset = xform.GetResetXformStack()
    time = Usd.TimeCode(frame) if mode == "frame" else Usd.TimeCode.Default()
    for channel, parts in values.items():
        name = "orient" if channel == "rotateXYZ" else channel
        op = ops[name]
        if name == "orient":
            euler = (rotation_format == "euler" if rotation_format else
                     channel == "rotateXYZ" or bool(op and op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ))
            op = ops[name] = rotation_op(xform, op, adjustment, euler)
            if euler and channel == "orient":
                parts = euler_from_quaternion(parts)
                reference = op.Get(time)
                if reference is not None:
                    parts = nearest_euler(parts, reference)
            elif not euler and channel == "rotateXYZ":
                parts = quaternion_from_euler(parts)
        if op is None:
            op = xform.AddXformOp(TYPES[name], UsdGeom.XformOp.PrecisionFloat, SUFFIX if adjustment else "")
            ops[name] = op
        attr = op.GetAttr()
        if mode == "frame":
            # Preserve weaker-layer samples when authoring a stronger sample map.
            samples = [(t, attr.Get(t)) for t in attr.GetTimeSamples()]
            for t, value in samples:
                attr.Set(Sdf.ValueBlock() if value is None else value, t)
        cls = type(attr.GetTypeName().defaultValue)
        value = cls(parts[0], type(attr.GetTypeName().defaultValue.GetImaginary())(*parts[1:])) if op.GetOpType() == UsdGeom.XformOp.TypeOrient else cls(*parts)
        if not all(math.isfinite(v) for v in encode_value(value)):
            raise ValueError("Transform exceeds this operation's floating-point precision.")
        if not attr.Set(value, time):
            raise ValueError("USD could not author this transform.")
    xform.SetXformOpOrder(base + [ops[name] for name in CHANNELS if ops[name]], reset)
    return str(prim.GetPath())


def transform_basis(prim, time):
    """Return pivot and parameter-space basis, including parent transforms."""
    xform, ops, adjustment = transform_ops(prim)
    parent = Gf.Matrix4d(1) if xform.GetResetXformStack() else xform.ComputeParentToWorldTransform(time)
    base_ops = [op for op in xform.GetOrderedXformOps() if op not in list(ops.values())]
    base = UsdGeom.Xformable.GetLocalTransformation(xform, base_ops, time) if adjustment and base_ops else Gf.Matrix4d(1)
    basis = base * parent
    world = xform.ComputeLocalToWorldTransform(time)
    return world.ExtractTranslation(), basis
