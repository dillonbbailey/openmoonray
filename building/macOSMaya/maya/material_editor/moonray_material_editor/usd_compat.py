"""USD API differences between Maya's USD 25.11 and the jobs' USD 22.11.

Background jobs (run-job.sh) use the workspace's MoonRay build, which has USD
22.11; MoonLab's workers were written for USD 25.
"""


def set_display_name(prim, name):
    # UsdPrim display names are newer than USD 22.11; they only label prims in UIs.
    if hasattr(prim, "SetDisplayName"):
        prim.SetDisplayName(name)
