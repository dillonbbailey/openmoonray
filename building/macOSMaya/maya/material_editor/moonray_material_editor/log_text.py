"""Convert renderer terminal output into complete, readable plain-text lines."""
import re


# CSI covers colors and cursor commands; OSC covers titles and hyperlink wrappers.
_ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[ -/]*[@-Z\\-_])")
_INCOMPLETE_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*)?$")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_log_text(text):
    return _CONTROLS.sub("", _INCOMPLETE_ESCAPE.sub("", _ESCAPES.sub("", text)))


class LogStream:
    """Buffer partial lines so QProcess chunk boundaries cannot split text/codes."""
    def __init__(self):
        self.buffer = b""

    def feed(self, data, final=False):
        parts = re.split(rb"[\r\n]", self.buffer + data)
        self.buffer = b"" if final else parts.pop()
        lines = [clean_log_text(part.decode("utf-8", "replace")) for part in parts]
        return "\n".join(line for line in lines if line.strip())
