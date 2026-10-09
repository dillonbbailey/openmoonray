"""Virtual joint rows and layered, per-skeleton animation edits."""
import math

from pxr import Gf, Usd, UsdGeom, UsdSkel, Vt

from .usd_editing import editable_prim, encode_value
from .usd_joint_paths import joint_path
from .usd_transform_lock import lock_info, require_unlocked
from .usd_transforms import checked_values, euler_from_quaternion, quaternion_from_euler, sample_info

OVERRIDE_KEY = "lunatic:jointOverride"
ATTRIBUTES = dict(translate="translations", orient="rotations", scale="scales")


def skeleton_joints(prim):
    skeleton = UsdSkel.Skeleton(prim) if prim and prim.IsActive() else None
    return list(map(str, skeleton.GetJointsAttr().Get() or [])) if skeleton else []


def joint_rows(prim, parent=None):
    joints = skeleton_joints(prim)
    if not joints:
        return []
    topology = UsdSkel.Topology(Vt.TokenArray(joints))
    valid, reason = topology.Validate()
    if not valid:
        return []
    parents = list(topology.GetParentIndices())
    has_children = set(parents)
    index = joints.index(parent) if parent in joints else -1
    if parent is not None and index == -1:
        return []
    skeleton = str(prim.GetPath())
    return [dict(name=joint.rsplit("/", 1)[-1], path=joint_path(skeleton, joint), type="Joint",
                 joint=joint, skeleton=skeleton, joint_index=i, children=i in has_children,
                 active=True, editable=False, add_children=False, bind_material=False)
            for i, joint in enumerate(joints) if parents[i] == index]


def joint_query(prim, joint):
    joints = skeleton_joints(prim)
    if joint not in joints:
        raise ValueError("This skeleton joint is no longer available.")
    valid, reason = UsdSkel.Topology(Vt.TokenArray(joints)).Validate()
    if not valid:
        raise ValueError("Invalid skeleton topology: " + reason)
    cache = UsdSkel.Cache()
    query = cache.GetSkelQuery(UsdSkel.Skeleton(prim))
    if not query:
        raise ValueError("The skeleton has invalid rest or bind transforms.")
    return query, joints, joints.index(joint)


def local_components(query, time):
    transforms = query.ComputeJointLocalTransforms(time)
    if transforms is None or len(transforms) != len(query.GetJointOrder()):
        raise ValueError("Could not evaluate the skeleton's joint transforms. Check its rest pose and animation arrays.")
    result = UsdSkel.DecomposeTransforms(transforms)
    if result is None:
        raise ValueError("Could not decompose the joint transforms into TRS.")
    return result


def joint_matrices(prim, joint, time):
    """Joint-local, parent-world and joint-world matrices at the requested time."""
    query, joints, index = joint_query(prim, joint)
    local = query.ComputeJointLocalTransforms(time)
    skeleton_space = query.ComputeJointSkelTransforms(time)
    if local is None or skeleton_space is None or len(local) != len(joints) or len(skeleton_space) != len(joints):
        raise ValueError("Could not evaluate this joint's pose.")
    skeleton_world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time)
    parent_index = query.GetTopology().GetParentIndices()[index]
    parent = skeleton_space[parent_index] * skeleton_world if parent_index >= 0 else skeleton_world
    return local[index], parent, skeleton_space[index] * skeleton_world


def joint_gizmo_axes(local, parent, space):
    axes = [Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1)]
    if space == "world":
        return axes
    rotation = Gf.Matrix4d().SetRotate(Gf.Transform(local).GetRotation())
    basis = rotation * parent
    return [basis.TransformDir(axis).GetNormalized() for axis in axes]


def joint_gizmo_values(local, parent, values, tool, axis, amount, space):
    """Convert a drag to joint TRS without adding xformOps to the skeleton."""
    from .usd_transform_pose import decompose, inverse, manipulated_world
    if tool == "translate":
        target = manipulated_world(local * parent, tool, axis, amount, space)
        # Local axes follow the actual parent chain, including scaled parents.
        if space == "local" and axis >= 0:
            target.SetTranslateOnly((local * parent).ExtractTranslation() + joint_gizmo_axes(local, parent, space)[axis] * amount)
        return dict(translate=list((target * inverse(parent, "the parent joint transform")).ExtractTranslation()))
    if tool == "scale" and (space == "local" or axis < 0):
        return dict(scale=[value * (amount if axis < 0 or i == axis else 1) for i, value in enumerate(values["scale"])])
    if tool == "orient" and space == "local":
        parts = values["orient"]
        rotation = Gf.Matrix4d().SetRotate(Gf.Quatd(parts[0], Gf.Vec3d(*parts[1:])))
        delta = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(*[int(i == axis) for i in range(3)]), amount))
        q = (delta * rotation).ExtractRotationQuat()
        return dict(orient=[q.GetReal(), *q.GetImaginary()])
    target = manipulated_world(local * parent, tool, axis, amount, space)
    changed, shear = decompose(target * inverse(parent, "the parent joint transform"))
    if shear:
        raise ValueError("This World-space gesture would shear the joint. Hold W, E or R and choose Local; UsdSkel joints store TRS only.")
    # World-axis gestures can affect both orientation and scale. Retain the
    # local translation exactly so rotation/scale do not move the joint pivot.
    return {name: changed[name] for name in ("orient", "scale")
            if any(abs(a - b) > 1e-9 for a, b in zip(changed[name], values[name]))}


def joint_info(prim, joint, frame, mode="auto", representation="euler"):
    path = joint_path(str(prim.GetPath()), joint)
    result = dict(path=path, joint=joint, skeleton=str(prim.GetPath()), frame=frame,
                  editable=False, space="local", representation="trs" if representation == "trs" else "euler",
                  **lock_info(prim))
    result["transform_lock_editable"] = False
    try:
        query, joints, index = joint_query(prim, joint)
        animation = query.GetAnimQuery()
        times = animation.GetJointTransformTimeSamples() if animation else []
        mode = ("frame" if times else "default") if mode == "auto" else mode
        components = local_components(query, Usd.TimeCode(frame) if mode == "frame" else Usd.TimeCode.Default())
        values = {name: encode_value(array[index]) for name, array in zip(ATTRIBUTES, components)}
        values["rotateXYZ"] = euler_from_quaternion(values["orient"])
        editable = (editable_prim(prim, children=True) and not prim.IsInstance() and
                    prim.GetStage().GetEditTarget().GetLayer().permissionToEdit and not result["transforms_locked"])
        result.update(values=values, time=mode, animated=bool(times), editable=editable, channels={})
        if animation:
            source = animation.GetPrim()
            result["sample_path"] = str(source.GetPath())
            for channel, name in ATTRIBUTES.items():
                result["channels"][channel] = dict(name=name, **sample_info(source.GetAttribute(name), frame))
            result["channels"]["rotateXYZ"] = result["channels"]["orient"]
        result["note"] = "Joint-local TRS relative to its parent joint. Edits use a skeleton-specific SkelAnimation override in the selected layer; the bind pose and source animation are preserved."
        if not editable:
            result["reason"] = (result["transform_lock_reason"] or
                                "Joint edits require an editable skeleton outside an instance and a writable edit target.")
    except (ValueError, RuntimeError) as exc:
        result["reason"] = str(exc)
    return result


def write_joint(prim, joint, values, frame=0, mode="default"):
    values = checked_values(values)
    if type(frame) not in (int, float) or not math.isfinite(frame) or mode not in ("default", "frame"):
        raise ValueError("Choose a valid joint frame and editing mode.")
    require_unlocked(prim)
    if not prim or not editable_prim(prim, children=True) or prim.IsInstance():
        raise ValueError("Joint edits require an editable skeleton outside an instance.")
    query, joints, index = joint_query(prim, joint)
    animation_query = query.GetAnimQuery()
    source = animation_query.GetPrim() if animation_query else None
    order = list(map(str, animation_query.GetJointOrder())) if animation_query else []
    time = Usd.TimeCode(frame) if mode == "frame" else Usd.TimeCode.Default()
    # Read before authoring any binding/order opinions so fallback and sparse
    # animation values are evaluated against the original composed skeleton.
    evaluated = local_components(query, time)
    expand = joint not in order
    snapshots = {}
    if expand:
        times = animation_query.GetJointTransformTimeSamples() if animation_query else []
        for sample in [None, *times]:
            snapshots[sample] = local_components(query, Usd.TimeCode.Default() if sample is None else Usd.TimeCode(sample))

    own_override = bool(source and source.GetParent() == prim and source.GetCustomDataByKey(OVERRIDE_KEY))
    if own_override:
        animation = UsdSkel.Animation(source)
    else:
        path = prim.GetPath().AppendChild("LunaticJointAnimation")
        suffix = 1
        while prim.GetStage().GetPrimAtPath(path):
            path = prim.GetPath().AppendChild("LunaticJointAnimation_" + str(suffix))
            suffix += 1
        animation = UsdSkel.Animation.Define(prim.GetStage(), path)
        if source:
            # An internal reference retains blend shapes, metadata and the
            # original joint ordering without changing a shared animation.
            animation.GetPrim().GetReferences().AddInternalReference(source.GetPath())
        animation.GetPrim().SetCustomDataByKey(OVERRIDE_KEY, True)
        UsdSkel.BindingAPI.Apply(prim).CreateAnimationSourceRel().SetTargets([path])
        if UsdSkel.BindingAPI(prim).GetInheritedAnimationSource() != animation.GetPrim():
            raise ValueError("The animation binding is overridden. Choose a stronger edit target, such as the session layer.")

    attrs = [animation.CreateTranslationsAttr(), animation.CreateRotationsAttr(), animation.CreateScalesAttr()]
    if expand:
        animation.CreateJointsAttr(joints)
        if list(animation.GetJointsAttr().Get()) != joints:
            raise ValueError("The joint order is overridden. Choose a stronger edit target.")
        order = joints
        # Changing an array's joint order requires remapping every existing
        # sample, including the other joints and the default/rest fallback.
        for sample, components in snapshots.items():
            for attr, array in zip(attrs, components):
                attr.Set(array, Usd.TimeCode.Default() if sample is None else Usd.TimeCode(sample))
    target_index = order.index(joint)
    for channel, parts in values.items():
        name = "orient" if channel == "rotateXYZ" else channel
        if channel == "rotateXYZ":
            parts = quaternion_from_euler(parts)
        attr = animation.GetPrim().GetAttribute(ATTRIBUTES[name])
        if mode == "frame":
            # A stronger time-sample map hides weaker maps; retain their keys.
            samples = [(t, attr.Get(t)) for t in attr.GetTimeSamples()]
            for t, array in samples:
                attr.Set(array, t)
        array = attr.Get(time)
        if array is None or len(array) != len(order):
            component = evaluated[list(ATTRIBUTES).index(name)]
            fallback = Gf.Quatf(1) if name == "orient" else Gf.Vec3h(1) if name == "scale" else Gf.Vec3f(0)
            array = type(component)([component[joints.index(token)] if token in joints else fallback for token in order])
        else:
            array = type(array)(array)
        value = (Gf.Quatf(parts[0], Gf.Vec3f(*parts[1:])) if name == "orient" else
                 Gf.Vec3h(*parts) if name == "scale" else Gf.Vec3f(*parts))
        if not all(math.isfinite(v) for v in encode_value(value)):
            raise ValueError("Joint TRS exceeds USD's component precision.")
        array[target_index] = value
        if not attr.Set(array, time):
            raise ValueError("USD could not author this joint transform.")
        if attr.Get(time) != array:
            raise ValueError("The joint transform is overridden. Choose a stronger edit target, such as the session layer.")
    return str(prim.GetPath())
