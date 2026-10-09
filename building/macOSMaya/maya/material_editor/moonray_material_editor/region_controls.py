"""Shared region selection state and controls for both image viewers."""
import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QWidget

from .buttons import FittingPushButton


class RegionSelection(QObject):
    changed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.enabled = False
        self.locked = False
        self.region = None
        self.bounds = QRectF()
        self.anchor = None

    @property
    def active(self):
        return list(self.region) if self.enabled and self.region else None

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.anchor = None
        self.changed.emit()

    def clear(self):
        self.region = self.anchor = None
        self.locked = False
        self.changed.emit()

    def set_locked(self, locked):
        self.locked = bool(locked and self.region is not None)
        self.anchor = None
        self.changed.emit()

    def set_bounds(self, bounds):
        if self.bounds != bounds:
            self.bounds = QRectF(bounds)
            self.clear()

    def begin(self, point, *, allow_outside=False):
        if self.locked or not self.enabled or self.bounds.isEmpty():
            return False
        if not allow_outside and not self.bounds.contains(point):
            return False
        self.anchor = self.clamp(point)
        self.move(point)
        return True

    def clamp(self, point):
        return QPointF(max(self.bounds.left(), min(self.bounds.right() - 1, math.floor(point.x()))),
                       max(self.bounds.top(), min(self.bounds.bottom() - 1, math.floor(point.y()))))

    def move(self, point):
        if self.locked or self.anchor is None:
            return False
        point = self.clamp(point)
        a = self.anchor
        self.region = [int(min(a.x(), point.x())), int(min(a.y(), point.y())),
                       int(max(a.x(), point.x())) + 1, int(max(a.y(), point.y())) + 1]
        self.changed.emit()
        return True

    def end(self, point):
        selected = self.move(point)
        self.anchor = None
        return selected


class RegionControls(QWidget):
    def __init__(self, selection, *, compact=False, lockable=False):
        super().__init__()
        self.setObjectName("render_region_controls")
        self.selection = selection
        self.compact = compact
        self.description_text = ""
        row = QHBoxLayout(self)
        row.setSpacing(2)
        row.setContentsMargins(0, 0, 0, 0)
        self.toggle = QCheckBox("Render region")
        self.toggle.setToolTip("Enable, then drag a red box on the image. The next render updates only those pixels. Uncheck for a full render.")
        self.toggle.toggled.connect(selection.set_enabled)
        row.addWidget(self.toggle)
        self.lock = QCheckBox("Lock region") if lockable else None
        if self.lock is not None:
            self.lock.setObjectName("lock_render_region")
            self.lock.setToolTip("Keep the selected region when clicking or dragging on the image. Uncheck to draw a new region; Clear removes it.")
            self.lock.toggled.connect(selection.set_locked)
            row.addWidget(self.lock)
        self.description = QLabel()
        row.addWidget(self.description, 1)
        self.reset = FittingPushButton("Clear")
        self.reset.clicked.connect(selection.clear)
        row.addWidget(self.reset)
        if compact:
            self.description.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.description.installEventFilter(self)
            self.reset.setFixedSize(40, 19)
            self.reset.setStyleSheet("padding: 0;")
        self.reset.setToolTip("Clear the selected render region")
        selection.changed.connect(self.refresh)
        self.refresh()

    def eventFilter(self, watched, event):
        if watched is self.description and event.type() == QEvent.Type.Resize:
            self.update_description()
        return super().eventFilter(watched, event)

    def update_description(self):
        text = self.description_text
        if self.compact:
            text = self.description.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, self.description.width())
        self.description.setText(text)

    def refresh(self):
        selection = self.selection
        with QSignalBlocker(self.toggle):
            self.toggle.setChecked(selection.enabled)
        if self.lock is not None:
            with QSignalBlocker(self.lock):
                self.lock.setChecked(selection.locked)
            self.lock.setEnabled(selection.enabled and selection.region is not None)
        self.reset.setEnabled(selection.region is not None)
        region = selection.active
        text = (f"{region[2]-region[0]} × {region[3]-region[1]} px" if region else
                "Render an image first" if selection.enabled and selection.bounds.isEmpty() else
                "Drag on image" if selection.enabled else "Full")
        self.description_text = text
        self.update_description()
        detail = f"Pixels [{region[0]}, {region[1]}] to [{region[2]-1}, {region[3]-1}] (top-left origin)" if region else ""
        self.description.setToolTip(text + ("\n" + detail if detail else ""))
        self.setToolTip(self.description.toolTip())
