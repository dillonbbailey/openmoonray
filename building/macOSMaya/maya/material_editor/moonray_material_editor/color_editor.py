"""RGB/HSV component controls with scene-linear color storage."""
import colorsys

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QHBoxLayout, QSizePolicy, QWidget

from .numeric_controls import NumericSpinBox

from .color_ramp import linear_color


def hsv_components(rgb):
    # HSV uses the same sRGB colors as the visual picker. Keep values above 1
    # for HDR colors; negative RGB components remain editable in RGB mode.
    srgb = [max(0., 12.92 * v if v <= .0031308 else 1.055 * v ** (1 / 2.4) - .055) for v in rgb]
    return list(colorsys.rgb_to_hsv(*srgb))


def rgb_components(hsv):
    h, s, v = hsv
    return linear_color(colorsys.hsv_to_rgb(h % 1, s, v))


class ColorNumber(NumericSpinBox):
    pass


class ColorComponents(QWidget):
    value_changed = Signal(list)
    changed = Signal(list)
    mode_changed = Signal(str)

    def __init__(self, value, mode="RGB"):
        super().__init__()
        self._value, self._saved, self._hsv = None, None, [0., 0., 0.]
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.mode = QComboBox()
        self.mode.setObjectName("color_component_mode")
        self.mode.setAccessibleName("Color component mode")
        self.mode.addItems(["RGB", "HSV"])
        self.mode.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.mode.setToolTip("RGB: scene-linear components. HSV: normalized sRGB hue, saturation and value (0–1). Switching modes keeps the color unchanged. RGB and HSV value retain HDR support above 1.")
        self.mode.setCurrentText(mode if mode in {"RGB", "HSV"} else "RGB")
        row.addWidget(self.mode)
        self.fields = []
        for index in range(3):
            field = ColorNumber()
            field.setKeyboardTracking(False)
            field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            field.setMinimumWidth(30)
            field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            field.valueChanged.connect(lambda value, index=index: self.edit_component(index, value))
            field.editingFinished.connect(self.commit)
            row.addWidget(field, 1)
            self.fields.append(field)
        self.set_value(value)
        self.mode.currentTextChanged.connect(self.change_mode)

    def value(self):
        return list(self._value)

    def set_value(self, value):
        if self._value != value:
            self._hsv = hsv_components(value)
        self._value = list(value)
        self._saved = list(value)
        self.refresh()

    def refresh(self):
        hsv = self.mode.currentText() == "HSV"
        values = self._hsv if hsv else self._value
        names = ("Hue", "Saturation", "Value") if hsv else ("Linear R", "Linear G", "Linear B")
        # Match the RGB fields' 1e12 upper bound after the sRGB transfer curve.
        hdr_value_max = 1.055 * 1e12 ** (1 / 2.4) - .055
        for i, field in enumerate(self.fields):
            with QSignalBlocker(field):
                if hsv:
                    field.setRange(0, (1, 1, hdr_value_max)[i])
                else:
                    field.setRange(-1e12, 1e12)
                field.setSingleStep(.01)
                field.setSuffix("")
                field.setAccessibleName(names[i])
                field.setToolTip(("Hue · 0–1 (one full rotation)", "Saturation · 0–1", "Value · 0–1 sRGB brightness; above 1 supports HDR")[i]
                                 if hsv else names[i] + " · scene-linear (HDR and negative values supported)")
                field.setValue(values[i])

    def change_mode(self, mode):
        self.refresh()
        self.mode_changed.emit(mode)

    def edit_component(self, index, value):
        if self.mode.currentText() == "HSV":
            self._hsv[index] = value
            # Hue is still useful when choosing the next color from gray or
            # black. Preserve it without authoring a rounding-only RGB edit.
            if not (index == 0 and (self._hsv[1] == 0 or self._hsv[2] == 0)
                    or index == 1 and self._hsv[2] == 0):
                self._value = rgb_components(self._hsv)
        else:
            self._value[index] = value
            hsv = hsv_components(self._value)
            if hsv[1] == 0:
                hsv[0] = self._hsv[0]  # Retain the chosen hue through gray/black.
            if hsv[2] == 0:
                hsv[1] = self._hsv[1]
            self._hsv = hsv
        self.value_changed.emit(self.value())

    def commit(self):
        if self._value != self._saved:
            self._saved = self.value()
            self.changed.emit(self.value())
