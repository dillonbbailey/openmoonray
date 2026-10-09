"""Serializable stage/layer metadata validation shared with the Qt host."""
import math

DEFAULTS = dict(upAxis="Y", metersPerUnit=1.0)
NUMBERS = {"metersPerUnit", "startTimeCode", "endTimeCode", "framesPerSecond", "timeCodesPerSecond"}
TEXT = {"comment", "documentation"}
FIELDS = NUMBERS | TEXT | {"upAxis"}

# OpenUSD's TimeCode glossary distinguishes playback advice from time scaling:
# https://openusd.org/25.11/glossary.html#timecodes-scaled-to-real-time
TIME_RATE_TOOLTIPS = {
    "framesPerSecond": (
        "Advisory playback/presentation rate for viewers and timeline UIs. "
        "It does not set the time-code scale when timeCodesPerSecond is authored. "
        "Legacy fallback: if timeCodesPerSecond is unauthored, USD uses framesPerSecond; "
        "if neither is authored, the rate is 24."
    ),
    "timeCodesPerSecond": (
        "Time-coordinate scale: 24 means a difference of 24 time codes equals one second. "
        "USD uses differences between layer rates to rescale animation during composition. "
        "This does not specify how many samples are stored per second. "
        "If unauthored, USD falls back to framesPerSecond, then 24."
    ),
}


def validate_metadata(values, *, defaults=False, current=None):
    if not isinstance(values, dict) or set(values) - FIELDS:
        raise ValueError("Unsupported stage metadata field.")
    result = dict(DEFAULTS) if defaults else {}
    result.update(values)
    for name, value in result.items():
        if name == "upAxis" and value not in ("Y", "Z"):
            raise ValueError("Up axis must be Y or Z.")
        if name in NUMBERS:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(name + " must be a finite number.")
            if name in ("metersPerUnit", "framesPerSecond", "timeCodesPerSecond") and value <= 0:
                raise ValueError(name + " must be greater than zero.")
            if name in ("startTimeCode", "endTimeCode") and not -(2**31) <= value <= 2**31 - 1:
                raise ValueError("Frame range must fit the viewport's 32-bit frame controls.")
            result[name] = float(value)
        elif name in TEXT and not isinstance(value, str):
            raise ValueError(name + " must be text.")
    combined = dict(current or {}, **result)
    if combined.get("startTimeCode", 0) > combined.get("endTimeCode", 0):
        raise ValueError("Start frame must be less than or equal to end frame.")
    return result
