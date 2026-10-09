"""Compact numeric presentation, independent of scene values and serialization."""
import math
import re
from decimal import Decimal

DISPLAY_DECIMALS = 4
_JSON_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')


def value_decimals(value, minimum=DISPLAY_DECIMALS):
    """Retain meaningful digits from the shortest representation of a float."""
    if not math.isfinite(value):
        return minimum
    number = Decimal(str(float(value))).normalize()
    digits = number.as_tuple()
    scientific = abs(value) >= 10000 or 0 < abs(value) < 0.0001
    places = len(digits.digits) - 1 if scientific else max(0, -digits.exponent)
    return max(minimum, min(17, places))


def compact_number(value, places=DISPLAY_DECIMALS):
    if not math.isfinite(value):
        return str(value)
    magnitude = abs(value)
    scientific = magnitude >= 10000 or 0 < magnitude < 0.0001
    text = format(value, f".{places}{'e' if scientific else 'f'}")
    if not scientific and abs(float(text)) >= 10000:
        text = format(value, f".{places}e")
    return text


def compact_json(text, places=DISPLAY_DECIMALS, *, preserve_precision=False):
    """Format floating literals while leaving integers, strings and paths intact."""
    def replace(match):
        token = match[0]
        if token.startswith('"') or not any(c in token for c in '.eE'):
            return token
        value = float(token)
        return compact_number(value, value_decimals(value, places) if preserve_precision else places)
    return _JSON_TOKENS.sub(replace, text)
