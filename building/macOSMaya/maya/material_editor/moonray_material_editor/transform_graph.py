"""Metadata shared by the editor and native USD transform reader."""


def projection_matrix_mode(catalog, shader):
    attrs = catalog.attributes(shader)
    if attrs.get("projection_matrix", {}).get("attrType") not in {"Mat4f", "Mat4d"}:
        return None
    return attrs.get("projection_mode", {}).get("enum", {}).get("projection_matrix")


def transform_info(prim, scene, frame=0):
    from pxr import Usd, UsdGeom
    if not prim or not prim.IsActive() or not UsdGeom.Xformable(prim):
        raise ValueError("Choose an active USD prim with a transform.")
    matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode(frame))
    return dict(path=str(prim.GetPath()), values=dict(projection_matrix=[list(row) for row in matrix]),
                reference=dict(scene=scene, path=str(prim.GetPath()), frame=frame))
