"""UI identities for array-backed joints, distinct from real USD prim paths."""


def joint_path(skeleton, joint):
    return skeleton + "#" + joint


def split_joint_path(path):
    if isinstance(path, str) and "#" in path:
        skeleton, joint = path.split("#", 1)
        if skeleton.startswith("/") and joint:
            return skeleton, joint
    return None
