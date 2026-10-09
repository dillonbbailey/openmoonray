"""RGB grading coefficients with independent fields and a shared color picker."""
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .numeric_controls import NumericSpinBox

from .color_editor import ColorNumber
from .color_picker import NormalizedColorPicker


def picker_range(value):
    return min(0., min(value)), max(1., max(value))


def picker_color(value, low, high):
    return QColor.fromRgbF(*(max(0., min(1., (v - low) / (high - low))) for v in value))


class ColorGradeEditor(QWidget):
    changed = Signal(list)
    value_changed = Signal(list)
    input_toggled = Signal(int)
    input_disconnected = Signal(int)

    def __init__(self, value, *, inputs=None, picker=True, parent=None):
        super().__init__(parent)
        self._value = list(value)
        self._saved = list(value)
        self.fields = []
        self.inputs = inputs or [dict(source="", visible=False) for _ in range(3)]
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(3)
        column.addLayout(row)
        for index, channel in enumerate("RGB"):
            row.addWidget(QLabel(channel))
            field = ColorNumber()
            field.setRange(-1e12, 1e12)
            field.setSingleStep(.05)
            field.setKeyboardTracking(False)
            field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            field.setMinimumWidth(30)
            field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            field.setAccessibleName(channel + " grading value")
            field.setToolTip(channel + " grading coefficient; negative values and values above 1 are supported.")
            field.setValue(value[index])
            field.setEnabled(not self.inputs[index]["source"])
            field.valueChanged.connect(lambda v, i=index: self.edit_component(i, v))
            field.editingFinished.connect(self.commit)
            self.fields.append(field)
            row.addWidget(field, 1)
        linked = any(entry["source"] for entry in self.inputs)
        self.swatch = None
        if picker:
            self.swatch = QPushButton()
            self.swatch.setObjectName("grade_color_picker")
            self.swatch.setFixedSize(24, 24)
            self.swatch.setAccessibleName("Pick grading color")
            self.swatch.setToolTip("Adjust R/G/B together with an HSV/RGB picker. Picker range supports negative values and values above 1.")
            self.swatch.setEnabled(not linked)
            if linked:
                self.swatch.setToolTip("A channel is connected to a map. Disconnect it to edit all three values with the color picker.")
            self.swatch.clicked.connect(self.pick_color)
            row.addWidget(self.swatch)
        if inputs is not None:
            ports = QHBoxLayout()
            ports.setSpacing(3)
            for index, channel in enumerate("RGB"):
                button = QPushButton(("● " if inputs[index]["visible"] else "○ ") + channel)
                button.setObjectName("grade_input_" + channel.lower())
                button.setToolTip("Show or hide the " + channel + " input socket")
                button.clicked.connect(lambda _=False, i=index: self.input_toggled.emit(i))
                ports.addWidget(button)
            ports.addStretch(1)
            column.addLayout(ports)
            for index, entry in enumerate(inputs):
                if entry["source"]:
                    connection = QHBoxLayout()
                    label = QLabel("RGB"[index] + " ↳ " + entry["source"])
                    label.setWordWrap(True)
                    connection.addWidget(label, 1)
                    unlink = QPushButton("Unlink")
                    unlink.setObjectName("grade_unlink_" + "rgb"[index])
                    unlink.clicked.connect(lambda _=False, i=index: self.input_disconnected.emit(i))
                    connection.addWidget(unlink)
                    column.addLayout(connection)
        self.refresh()

    def value(self):
        return list(self._value)

    def refresh(self):
        for field, value in zip(self.fields, self._value):
            with QSignalBlocker(field):
                field.setValue(value)
        if self.swatch:
            color = picker_color(self._value, *picker_range(self._value))
            self.swatch.setStyleSheet(f"background: {color.name()}; border: 1px solid #7c8c96;")

    def edit_component(self, index, value):
        if self.inputs[index]["source"]:
            return
        self._value[index] = value
        self.refresh()
        self.value_changed.emit(self.value())

    def commit(self):
        if self._saved != self._value:
            self._saved = self.value()
            self.changed.emit(self.value())

    def pick_color(self):
        dialog = GradeColorDialog(self.value(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._value = dialog.value()
            self.refresh()
            self.value_changed.emit(self.value())
            self.commit()
        dialog.deleteLater()


class GradeColorDialog(QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adjust grading channels")
        layout = QVBoxLayout(self)
        self.components = ColorGradeEditor(value, picker=False)
        layout.addWidget(self.components)
        row = QHBoxLayout()
        row.addWidget(QLabel("Picker range"))
        self.minimum, self.maximum = NumericSpinBox(), NumericSpinBox()
        for field, label, number in zip((self.minimum, self.maximum), ("Minimum", "Maximum"), picker_range(value)):
            field.setRange(-1e12, 1e12)
            field.setKeyboardTracking(False)
            field.setAccessibleName(label + " picker value")
            field.setValue(number)
            row.addWidget(QLabel(label))
            row.addWidget(field, 1)
            field.valueChanged.connect(self.sync_picker)
        layout.addLayout(row)
        self.picker = NormalizedColorPicker(self)
        layout.addWidget(self.picker)
        self.note = QLabel("Picker colors map to the numeric range above. Edit the R/G/B fields for exact values.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.picker.currentColorChanged.connect(self.picked)
        self.components.value_changed.connect(self.numeric_changed)
        self.sync_picker()

    def value(self):
        return self.components.value()

    def sync_picker(self, *_):
        low, high = self.minimum.value(), self.maximum.value()
        valid = low < high
        self.picker.setEnabled(valid)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)
        if valid:
            with QSignalBlocker(self.picker):
                self.picker.setCurrentColor(picker_color(self.value(), low, high))

    def numeric_changed(self, value):
        if min(value) < self.minimum.value():
            with QSignalBlocker(self.minimum):
                self.minimum.setValue(min(value))
        if max(value) > self.maximum.value():
            with QSignalBlocker(self.maximum):
                self.maximum.setValue(max(value))
        self.sync_picker()

    def picked(self, color):
        low, high = self.minimum.value(), self.maximum.value()
        if low < high:
            self.components._value = [low + c * (high - low) for c in (color.redF(), color.greenF(), color.blueF())]
            self.components.refresh()
