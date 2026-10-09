"""A compact render percentage indicator shared by both image viewers."""
import re

from PySide6.QtWidgets import QProgressBar, QVBoxLayout
from .themes import set_local_style


_PERCENT = re.compile(r"^Render progress: (\d+)%$", re.MULTILINE)


def logged_percent(text, current=0):
    values = [min(99, int(value)) for value in _PERCENT.findall(text) if int(value) <= 100]
    return max([current, *values])


class RenderProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("render_progress_bar")
        self.setAccessibleName("Render progress")
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(3)
        self.setContentsMargins(0, 0, 0, 0)
        set_local_style(self, "QProgressBar { border: 0; padding: 0; margin: 0; border-radius: 0; background: #252a32; }"
                        "QProgressBar::chunk { border: 0; margin: 0; border-radius: 0; background: #61b8dc; }",
                        "QProgressBar { border: 0; padding: 0; margin: 0; border-radius: 0; }"
                        "QProgressBar::chunk { border: 0; margin: 0; border-radius: 0; }",
                        # Three pixels leave no room for Fusion's native groove
                        # and chunk borders. Paint this compact bar explicitly.
                        explorer="QProgressBar { border: 0; padding: 0; margin: 0; border-radius: 0; background: palette(shadow); }"
                                 "QProgressBar::chunk { border: 0; margin: 0; border-radius: 0; background: palette(highlight); }")
        self.valueChanged.connect(lambda value: self.setToolTip(f"Render progress: {value}%"))
        self.setToolTip("Render progress: 0%")

    def under(self, image):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(image, 1)
        layout.addWidget(self)
        return layout

    def consume(self, text):
        for line in text.splitlines():
            if line == "Render progress: busy":
                self.setRange(0, 0)
                self.setToolTip("Rendering · waiting for measurable progress")
            elif _PERCENT.fullmatch(line):
                if self.maximum() == 0:
                    self.setRange(0, 100)
                    self.setValue(0)
                self.setValue(logged_percent(line, self.value()))
