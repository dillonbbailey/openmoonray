"""Color stops, positions and interpolation edited as one atomic parameter."""
import copy

from PySide6.QtCore import QPointF, QRectF, Qt, QSignalBlocker, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout,
                              QLabel, QSizePolicy, QVBoxLayout, QWidget)

from .numeric_controls import NumericSpinBox
from .number_format import compact_number

from .buttons import FittingPushButton
from .color_editor import ColorComponents
from .color_picker import NormalizedColorDialog
from .color_ramp import INTERPOLATIONS, MAX_STOPS, display_color, linear_color, read_stops, sample_ramp


class RampNumber(NumericSpinBox):
    pass


class RampStrip(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.drag_before = None
        self.drag_domain = None
        self.setMinimumWidth(180)
        self.setFixedHeight(72)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Color ramp stops")
        self.setToolTip("Drag a stop to move it. Double-click the ramp to add a stop; double-click a marker to edit its color. Escape cancels a drag.")

    def domain(self):
        if self.drag_domain is not None:
            return self.drag_domain
        positions = [s["position"] for s in self.editor.stops]
        return min([0.] + positions), max([1.] + positions)

    def bar(self):
        return QRectF(9, 4, max(1, self.width() - 18), 28)

    def stop_x(self, index):
        lo, hi = self.domain()
        return self.bar().left() + (self.editor.stops[index]["position"] - lo) / (hi-lo) * self.bar().width()

    def position_at(self, x):
        lo, hi = self.domain()
        return lo + max(0., min(1., (x-self.bar().left()) / self.bar().width())) * (hi-lo)

    def hit(self, point):
        if not self.bar().bottom() - 3 <= point.y() <= self.bar().bottom() + 17:
            return None
        hits = [i for i in range(len(self.editor.stops)) if abs(self.stop_x(i)-point.x()) <= 8]
        return self.editor.selected if self.editor.selected in hits else hits[0] if hits else None

    def paintEvent(self, event):
        painter = QPainter(self)
        bar = self.bar()
        for x in range(int(bar.width())):
            color = sample_ramp(self.editor.stops, self.position_at(bar.left()+x), self.editor.space)
            painter.fillRect(QRectF(bar.left()+x, bar.top(), 1, bar.height()), QColor.fromRgbF(*display_color(color)))
        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.drawRect(bar)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        indices = [i for i in range(len(self.editor.stops)) if i != self.editor.selected]
        if self.editor.selected >= 0:
            indices.append(self.editor.selected)
        for i in indices:
            x, y = self.stop_x(i), bar.bottom() + 1
            selected = i == self.editor.selected
            painter.setPen(QPen(self.palette().highlight().color() if selected else self.palette().text().color(), 2 if selected else 1))
            painter.setBrush(QColor.fromRgbF(*display_color(self.editor.stops[i]["color"])))
            painter.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x+6, y+6), QPointF(x+6, y+15),
                                           QPointF(x-6, y+15), QPointF(x-6, y+6)]))
        painter.setPen(self.palette().text().color())
        lo, hi = self.domain()
        painter.drawText(QRectF(9, 50, self.width()-18, 22), Qt.AlignmentFlag.AlignLeft, compact_number(lo))
        painter.drawText(QRectF(9, 50, self.width()-18, 22), Qt.AlignmentFlag.AlignRight, compact_number(hi))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        index = self.hit(event.position())
        if index is not None:
            self.editor.select(index)
            self.drag_before = copy.deepcopy(self.editor.stops)
            self.drag_domain = self.domain()
            self.setFocus()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_before is not None:
            self.editor.stops[self.editor.selected]["position"] = self.position_at(event.position().x())
            self.editor.refresh()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_before is not None:
            self.drag_before = self.drag_domain = None
            self.editor.commit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_before = self.drag_domain = None
            index = self.hit(event.position())
            if index is not None:
                self.editor.select(index)
                self.editor.pick_color()
            elif self.bar().adjusted(0, 0, 0, 17).contains(event.position()):
                self.editor.add_stop(self.position_at(event.position().x()))
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        if self.drag_before is not None:
            self.editor.stops = self.drag_before
            self.drag_before = self.drag_domain = None
            self.editor.refresh()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.drag_before is not None:
            self.editor.stops = self.drag_before
            self.drag_before = self.drag_domain = None
            self.editor.refresh()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.editor.remove_stop()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.editor.stops:
            delta = -1 if event.key() == Qt.Key.Key_Left else 1
            self.editor.select((self.editor.selected + delta) % len(self.editor.stops))
        else:
            return super().keyPressEvent(event)
        event.accept()


class ColorRampEditor(QWidget):
    changed = Signal(dict)
    selection_changed = Signal(int)
    color_mode_changed = Signal(str)
    input_toggled = Signal(int)
    input_disconnected = Signal(int)

    def __init__(self, fields, values, *, selected=0, space=0, four_corner=False, color_mode="RGB", color_inputs=None):
        super().__init__()
        self.fields, self.space = fields, space
        self.minimum = 4 if four_corner else 1
        self.maximum = 4 if four_corner else MAX_STOPS
        self.stops = read_stops(values[fields["values"]], values[fields["positions"]], values[fields["interpolation_types"]])
        for index, stop in enumerate(self.stops):
            stop["_index"] = index
        self.last_order = list(range(len(self.stops)))
        self.color_inputs = color_inputs
        self.saved = copy.deepcopy(self.stops)
        self.selected = min(max(0, selected), len(self.stops)-1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self.strip = RampStrip(self)
        outer.addWidget(self.strip)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(QLabel("Stop"))
        self.stop = QComboBox()
        self.stop.setAccessibleName("Selected color stop")
        self.stop.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.stop.activated.connect(self.select)
        row.addWidget(self.stop, 1)
        self.add = FittingPushButton("Add")
        self.remove = FittingPushButton("Remove")
        self.add.clicked.connect(lambda: self.add_stop())
        self.remove.clicked.connect(self.remove_stop)
        row.addWidget(self.add)
        row.addWidget(self.remove)
        outer.addLayout(row)
        self.controls = QWidget()
        form = QFormLayout(self.controls)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.position = self.number("Stop position")
        self.position.setSingleStep(.01)
        self.position.valueChanged.connect(self.change_position)
        self.position.editingFinished.connect(self.commit)
        form.addRow("Position", self.position)
        self.color = FittingPushButton("Choose color…")
        self.color.clicked.connect(self.pick_color)
        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        color_row.addWidget(self.color, 1)
        self.input = FittingPushButton("●")
        self.input.setFixedWidth(24)
        self.input.setAccessibleName("Toggle ramp color input")
        self.input.clicked.connect(lambda: self.input_toggled.emit(self.selected))
        self.input.setVisible(color_inputs is not None)
        color_row.addWidget(self.input)
        form.addRow("Color", color_row)
        self.binding = QWidget()
        binding_row = QHBoxLayout(self.binding)
        binding_row.setContentsMargins(0, 0, 0, 0)
        self.source = QLabel()
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setWordWrap(True)
        binding_row.addWidget(self.source, 1)
        self.disconnect = FittingPushButton("Disconnect")
        self.disconnect.clicked.connect(lambda: self.input_disconnected.emit(self.selected))
        binding_row.addWidget(self.disconnect)
        form.addRow(self.binding)
        self.components = ColorComponents(self.stops[self.selected]["color"] if self.selected >= 0 else [0., 0., 0.], color_mode)
        self.rgb = self.components.fields
        self.components.value_changed.connect(self.change_color)
        self.components.changed.connect(lambda _: self.commit())
        self.components.mode_changed.connect(self.color_mode_changed)
        form.addRow(self.components)
        self.interpolation = QComboBox()
        self.interpolation.setAccessibleName("Stop interpolation")
        self.interpolation.setToolTip("Interpolation from the selected stop to the next stop.")
        for value, name in enumerate(INTERPOLATIONS):
            self.interpolation.addItem(name, value)
        self.interpolation.activated.connect(self.change_interpolation)
        form.addRow("Interpolation", self.interpolation)
        outer.addWidget(self.controls)
        hint = QLabel("Drag stops to move · Double-click the ramp to add")
        if color_inputs is not None:
            hint.setText(hint.text() + "\nConnect maps to color inputs on the node. The strip shows saved colors; the node preview renders connected maps.")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        self.add.setToolTip("Four corner ramps use exactly four stops." if four_corner else f"Add a color stop (up to {MAX_STOPS}).")
        self.remove.setToolTip("Four corner ramps use exactly four stops." if four_corner else "Remove the selected stop.")
        self.refresh()

    @staticmethod
    def number(name):
        widget = RampNumber()
        widget.setAccessibleName(name)
        widget.setRange(-1e12, 1e12)
        widget.setSingleStep(.01)
        widget.setKeyboardTracking(False)
        widget.setMinimumWidth(30)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        return widget

    def select(self, index):
        self.selected = min(max(0, index), len(self.stops)-1)
        self.selection_changed.emit(self.selected)
        self.refresh()

    def refresh(self):
        with QSignalBlocker(self.stop):
            self.stop.clear()
            for i, stop in enumerate(self.stops):
                self.stop.addItem(f"{i+1} · {compact_number(stop['position'])}")
            self.stop.setCurrentIndex(self.selected)
        self.controls.setEnabled(self.selected >= 0)
        self.remove.setEnabled(len(self.stops) > self.minimum)
        self.add.setEnabled(len(self.stops) < self.maximum)
        if self.selected >= 0:
            stop = self.stops[self.selected]
            with QSignalBlocker(self.position):
                self.position.setValue(stop["position"])
            self.components.set_value(stop["color"])
            with QSignalBlocker(self.interpolation):
                self.interpolation.setCurrentIndex(stop["interpolation"])
            self.update_swatch()
        binding = self.color_inputs[self.selected] if self.color_inputs is not None and self.selected >= 0 else {}
        source = binding.get("source", "")
        self.source.setText(source)
        self.binding.setVisible(bool(source))
        self.color.setEnabled(not source)
        self.components.setEnabled(not source)
        self.input.setText("●" if binding.get("visible") else "○")
        self.input.setEnabled(not source)
        self.input.setToolTip(f"Show or hide color {self.selected + 1} on the node. Disconnect its map before hiding.")
        self.strip.update()

    def update_swatch(self):
        rgb = self.stops[self.selected]["color"]
        color = QColor.fromRgbF(*display_color(rgb))
        luminance = sum(weight * max(0., min(1., value)) for weight, value in zip((.2126, .7152, .0722), rgb))
        text = "#111111" if luminance > .179 else "#ffffff"
        self.color.setStyleSheet(f"QPushButton {{ background: {color.name()}; color: {text}; }}")
        self.color.setToolTip("Choose an sRGB color. Numeric RGB values are scene-linear and can exceed 1.")

    def change_position(self, value):
        if self.selected >= 0:
            self.stops[self.selected]["position"] = value
            self.strip.update()

    def change_color(self, value):
        if self.selected >= 0:
            self.stops[self.selected]["color"] = value
            self.update_swatch()
            self.strip.update()

    def change_interpolation(self, index):
        if self.selected >= 0:
            self.stops[self.selected]["interpolation"] = self.interpolation.itemData(index)
            self.commit()

    def pick_color(self):
        if self.selected < 0 or (self.color_inputs is not None and self.color_inputs[self.selected].get("source")):
            return
        selected = self.selected
        initial = QColor.fromRgbF(*display_color(self.stops[selected]["color"]))
        color = NormalizedColorDialog.getColor(initial, self, "Choose ramp color (HSV / RGB)")
        if color.isValid() and color != initial:
            self.stops[selected]["color"] = linear_color((color.redF(), color.greenF(), color.blueF()))
            self.commit()

    def add_stop(self, position=None):
        if len(self.stops) >= self.maximum:
            return
        if position is None:
            ordered = sorted([s["position"] for s in self.stops])
            spans = list(zip(ordered, ordered[1:]))
            position = sum(max(spans, key=lambda pair: pair[1]-pair[0])) / 2 if spans else .5
        color = sample_ramp(self.stops, position, self.space)
        interpolation = self.stops[self.selected]["interpolation"] if self.selected >= 0 else 1
        self.stops.append(dict(position=position, color=color, interpolation=interpolation, _index=None))
        self.selected = len(self.stops)-1
        self.commit()

    def remove_stop(self):
        if len(self.stops) <= self.minimum or self.selected < 0:
            return
        self.stops.pop(self.selected)
        self.selected = min(self.selected, len(self.stops)-1)
        self.commit()

    def values(self):
        return {self.fields[key]: [copy.deepcopy(stop[field]) for stop in self.stops]
                for key, field in (("values", "color"), ("positions", "position"), ("interpolation_types", "interpolation"))}

    def commit(self):
        if self.stops == self.saved:
            return
        selected = self.stops[self.selected] if self.selected >= 0 else None
        self.stops.sort(key=lambda stop: stop["position"])
        self.last_order = [stop["_index"] for stop in self.stops]
        if self.color_inputs is not None:
            self.color_inputs = [self.color_inputs[old] if old is not None else dict(visible=True)
                                 for old in self.last_order]
        for index, stop in enumerate(self.stops):
            stop["_index"] = index
        self.selected = next((i for i, stop in enumerate(self.stops) if stop is selected), -1)
        self.saved = copy.deepcopy(self.stops)
        self.selection_changed.emit(self.selected)
        self.refresh()
        self.changed.emit(self.values())
