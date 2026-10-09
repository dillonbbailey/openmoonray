"""Visual color picking with normalized floating-point HSV and RGB controls."""
from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
                              QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSlider,
                              QVBoxLayout, QWidget)

from .numeric_controls import NumericSpinBox
from .number_format import compact_number


class NormalizedNumber(NumericSpinBox):
    pass


class ColorPlane(QWidget):
    def __init__(self, picker, *, hue=False):
        super().__init__(picker)
        self.picker, self.hue = picker, hue
        self.setMinimumSize(24 if hue else 180, 140)
        if hue:
            self.setFixedWidth(26)
        self.setSizePolicy(QSizePolicy.Policy.Fixed if hue else QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName("Hue strip" if hue else "Saturation and value")

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        h, s, v = self.picker.hsv
        if self.hue:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            for i in range(7):
                gradient.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1, 1, 1))
            painter.fillRect(rect, gradient)
            point = QPointF(rect.center().x(), rect.top() + h * rect.height())
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0, QColor("white"))
            gradient.setColorAt(1, QColor.fromHsvF(h % 1, 1, 1))
            painter.fillRect(rect, gradient)
            shade = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            shade.setColorAt(0, QColor(0, 0, 0, 0))
            shade.setColorAt(1, QColor("black"))
            painter.fillRect(rect, shade)
            point = QPointF(rect.left() + s * rect.width(), rect.top() + (1 - v) * rect.height())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("black"), 3))
        painter.drawEllipse(point, 5, 5)
        painter.setPen(QPen(QColor("white"), 1))
        painter.drawEllipse(point, 5, 5)

    def choose(self, position):
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        x = max(0., min(1., (position.x() - rect.left()) / max(1., rect.width())))
        y = max(0., min(1., (position.y() - rect.top()) / max(1., rect.height())))
        h, s, v = self.picker.hsv
        self.picker.set_hsv(y if self.hue else h, s if self.hue else x, v if self.hue else 1 - y)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.choose(event.position())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.choose(event.position())
            event.accept()
        else:
            super().mouseMoveEvent(event)


class NormalizedColorPicker(QWidget):
    currentColorChanged = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color, self.hsv = QColor("white"), [0., 0., 1.]
        self.fields, self.sliders = {}, {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        visual = QHBoxLayout()
        self.plane, self.hue_strip = ColorPlane(self), ColorPlane(self, hue=True)
        visual.addWidget(self.plane, 1)
        visual.addWidget(self.hue_strip)
        layout.addLayout(visual, 1)
        groups = QHBoxLayout()
        for space in ("HSV", "RGB"):
            group = QGroupBox(space + " · 0.0–1.0")
            grid = QGridLayout(group)
            for row, channel in enumerate(space):
                label = QLabel(channel)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(0, 10000)
                slider.setSingleStep(100)
                slider.setPageStep(1000)
                slider.setMinimumWidth(80)
                slider.setObjectName("color_slider_" + channel.lower())
                slider.setAccessibleName(channel + " normalized slider")
                field = NormalizedNumber()
                field.setObjectName("color_value_" + channel.lower())
                field.setRange(0., 1.)
                field.setSingleStep(.01)
                field.setKeyboardTracking(False)
                field.setAccessibleName(channel + " normalized value")
                field.setToolTip(channel + " · normalized 0.0–1.0")
                field.setMinimumWidth(100)
                label.setBuddy(field)
                slider.valueChanged.connect(lambda value, c=channel: self.edit_channel(c, value / 10000))
                field.valueChanged.connect(lambda value, c=channel: self.edit_channel(c, value))
                self.fields[channel], self.sliders[channel] = field, slider
                grid.addWidget(label, row, 0)
                grid.addWidget(slider, row, 1)
                grid.addWidget(field, row, 2)
            groups.addWidget(group, 1)
        layout.addLayout(groups)
        row = QHBoxLayout()
        self.swatch = QLabel()
        self.swatch.setMinimumSize(60, 24)
        row.addWidget(self.swatch, 1)
        row.addWidget(QLabel("Hex"))
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setMaximumWidth(100)
        self.hex.setAccessibleName("Hex color")
        self.hex.editingFinished.connect(self.edit_hex)
        row.addWidget(self.hex)
        layout.addLayout(row)
        self.refresh()

    def currentColor(self):
        return QColor(self._color)

    def setCurrentColor(self, color):
        color = QColor(color)
        if not color.isValid():
            return
        h, s, v, _ = color.getHsvF()
        self.set_color(color, [self.hsv[0] if h < 0 else h, self.hsv[1] if v == 0 else s, v])

    def set_color(self, color, hsv):
        changed = color.getRgbF() != self._color.getRgbF()
        self._color, self.hsv = QColor(color), list(hsv)
        self.refresh()
        if changed:
            self.currentColorChanged.emit(self.currentColor())

    def set_hsv(self, h, s, v):
        self.set_color(QColor.fromHsvF(h % 1, s, v, self._color.alphaF()), [h, s, v])

    def edit_channel(self, channel, value):
        if channel in "HSV":
            hsv = list(self.hsv)
            hsv["HSV".index(channel)] = value
            self.set_hsv(*hsv)
        else:
            rgb = list(self._color.getRgbF())
            rgb["RGB".index(channel)] = value
            self.setCurrentColor(QColor.fromRgbF(*rgb))

    def edit_hex(self):
        text = self.hex.text().strip()
        if text.lower() != self._color.name():
            color = QColor(text)
            if color.isValid():
                self.setCurrentColor(color)
        self.refresh()

    def refresh(self):
        values = dict(zip("HSVRGB", [*self.hsv, *self._color.getRgbF()[:3]]))
        for channel, value in values.items():
            field, slider = self.fields[channel], self.sliders[channel]
            with QSignalBlocker(field), QSignalBlocker(slider):
                field.setValue(value)
                slider.setValue(round(value * 10000))
            slider.setToolTip(f"{channel} · {compact_number(value)} (0.0–1.0)")
        self.hex.setText(self._color.name())
        self.swatch.setStyleSheet("background-color: " + self._color.name() + "; border: 1px solid #777;")
        self.plane.update()
        self.hue_strip.update()


class NormalizedColorDialog(QDialog):
    def __init__(self, initial, parent=None, title="Choose color (HSV / RGB)"):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        self.picker = NormalizedColorPicker(self)
        self.picker.setCurrentColor(initial)
        layout.addWidget(self.picker)
        row = QHBoxLayout()
        self.reset = QPushButton("Original color")
        self.reset.clicked.connect(lambda: self.picker.setCurrentColor(initial))
        row.addWidget(self.reset)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
        self.resize(600, 420)

    @staticmethod
    def getColor(initial, parent=None, title="Choose color (HSV / RGB)"):
        dialog = NormalizedColorDialog(initial, parent, title)
        color = dialog.picker.currentColor() if dialog.exec() == QDialog.DialogCode.Accepted else QColor()
        dialog.deleteLater()
        return color
