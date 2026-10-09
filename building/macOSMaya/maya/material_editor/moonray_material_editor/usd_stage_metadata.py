"""Read effective stage metadata; author only validated layer opinions."""
from pxr import UsdGeom

from .stage_metadata import validate_metadata


def stage_metadata(stage):
    return dict(upAxis=str(UsdGeom.GetStageUpAxis(stage)), metersPerUnit=UsdGeom.GetStageMetersPerUnit(stage),
                startTimeCode=stage.GetStartTimeCode(), endTimeCode=stage.GetEndTimeCode(),
                framesPerSecond=stage.GetFramesPerSecond(), timeCodesPerSecond=stage.GetTimeCodesPerSecond(),
                comment=stage.GetMetadata("comment") or "", documentation=stage.GetMetadata("documentation") or "")


def author_layer_metadata(layer, values, *, defaults=False):
    values = validate_metadata(values, defaults=defaults)
    for name, value in values.items():
        layer.pseudoRoot.SetInfo(name, value)


def edit_stage_metadata(editing, values):
    values = validate_metadata(values, current=stage_metadata(editing.stage))
    # Root/session metadata governs the stage, regardless of the active sublayer.
    layer = editing.stage.GetSessionLayer()
    if editing.stage.IsLayerMuted(layer.identifier):
        raise ValueError("Unmute the Session layer before editing stage metadata.")
    def author():
        for name, value in values.items():
            layer.pseudoRoot.SetInfo(name, value)
    editing.change("Edit stage metadata", author, layer=layer)
    return "/"
