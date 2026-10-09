"""Persistent, per-prim protection for interactive USD transform edits."""
from pxr import UsdGeom

KEY = "moonrayEditor:lockTransforms"


def is_locked(prim):
    return bool(prim and prim.GetCustomDataByKey(KEY) is True)


def is_transform_property(name):
    return name == "xformOpOrder" or name.startswith("xformOp:")


def lock_info(prim):
    from .usd_editing import editable_prim
    from .usd_preview_camera import is_locked as preview_locked
    preview = preview_locked(prim)
    return dict(transforms_locked=is_locked(prim) or preview,
                transform_lock_editable=bool(prim and UsdGeom.Xformable(prim) and editable_prim(prim)
                                             and prim.GetStage().GetEditTarget().GetLayer().permissionToEdit),
                transform_lock_reason=("Preview camera transforms follow Material Editor. Unlock the preview camera in USD Settings to edit them."
                                       if preview else "Transforms are locked. Uncheck lock in the Transform tab to edit this prim." if is_locked(prim) else ""))


def require_unlocked(prim):
    info = lock_info(prim)
    if info["transforms_locked"]:
        raise ValueError(info["transform_lock_reason"])


def set_locked(prim, enabled):
    if type(enabled) is not bool:
        raise ValueError("Lock transforms must be on or off.")
    info = lock_info(prim)
    if not info["transform_lock_editable"]:
        raise ValueError(info["transform_lock_reason"] or "Select an editable geometry, Xform, camera, or light prim.")
    prim.SetCustomDataByKey(KEY, enabled)
    if is_locked(prim) != enabled:
        raise ValueError("The transform lock is overridden. Choose a stronger edit target, such as the session layer.")
    return str(prim.GetPath())
