"""USD purpose selections shared by the host and native workers."""

PURPOSES = ("guide", "proxy", "render")


def normalize_purposes(value):
    """Read independent selections and legacy single-purpose project settings."""
    if isinstance(value, str):
        value = list(PURPOSES) if value == "all" else [] if value == "default" else [value]
    if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or item not in PURPOSES for item in value):
        raise ValueError("USD purposes must contain only Guide, Proxy, or Render.")
    return [purpose for purpose in PURPOSES if purpose in value]
