"""Convert renderer-reported percentages into selected log milestones."""
import re

from .log_text import clean_log_text


PROGRESS_STEPS = (1, 5, 10)
DEFAULT_PROGRESS_STEP = 5
_PROGRESS = re.compile(r"Rendering\s*\[\s*(\d+(?:\.\d+)?)%\s*\]|(\d+(?:\.\d+)?)%\s+complete\b")


def validate_progress_step(value):
    if type(value) is not int or value not in PROGRESS_STEPS:
        raise ValueError("Choose a render progress increment of 1%, 5%, or 10%.")
    return value


class RenderProgress:
    """Report crossed thresholds once; reserve 100% for a successful process exit.

    Fast renders may cross several thresholds between renderer updates. No
    milestones are inferred from elapsed time, and preparation is not rendering.
    """

    def __init__(self, step=DEFAULT_PROGRESS_STEP):
        self.step = validate_progress_step(step)
        self.next_percent = 0
        self.finished = False

    def consume(self, line):
        match = _PROGRESS.search(clean_log_text(line))
        if not match:
            return None
        percent = float(match.group(1) or match.group(2))
        if not 0 <= percent <= 100 or self.finished:
            return []
        messages = []
        while self.next_percent <= percent and self.next_percent < 100:
            messages.append(f"Render progress: {self.next_percent}%")
            self.next_percent += self.step
        return messages

    def finish(self):
        if self.finished:
            return []
        self.finished = True
        return ["Render progress: 100%"]
