"""Image navigation for the material preview, independent of render settings."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from .model import GraphError
from .region_controls import RegionSelection


class PreviewPanel(QWidget):
    """A darker surround without changing the theme's native control style."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Window).darker(145))


class PreviewLabel(QLabel):
    zoom_changed = Signal(str)

    def __init__(self):
        super().__init__("A material, in its best light.\n\nRender a preview to begin.")
        self.setObjectName("preview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Wheel: zoom at cursor · Drag: pan · RMB / F / double-click: fit · 1: actual pixels · + / −: zoom")
        self.original = QPixmap()
        self._fit = True
        self._scale = 1.0
        self._pan = QPointF()
        self._drag = None
        self.selection = RegionSelection(self)
        self.selection.changed.connect(self.update)

    @property
    def image_scale(self):
        if self._fit and not self.original.isNull():
            return min(max(1, self.width() - 8) / self.original.width(),
                       max(1, self.height() - 8) / self.original.height())
        return self._scale

    def view_state(self):
        return self._fit, self._scale, self._pan.x(), self._pan.y()

    def restore_view(self, state):
        self._fit, self._scale, x, y = state
        self._pan = QPointF(x, y)
        self._drag = None
        self.unsetCursor()
        self.update_image()

    def set_image(self, path):
        image = QPixmap(str(path))
        if image.isNull():
            raise GraphError(f"Could not load the rendered preview: {path}")
        self.original = image
        self.update_image()

    def update_image(self):
        self.selection.set_bounds(QRectF(self.original.rect()))
        self.zoom_changed.emit("Fit" if self.original.isNull() else
                               f"{'Fit · ' if self._fit else ''}{self.image_scale:.0%}")
        self.update()

    def fit_image(self):
        self._fit = True
        self._pan = QPointF()
        self.update_image()

    def actual_size(self):
        self._fit = False
        self._scale = 1.0
        self._pan = QPointF()
        self.update_image()

    def zoom(self, factor, anchor=None):
        if self.original.isNull():
            return
        old = self.image_scale
        new = max(.01, min(32.0, old * factor))
        center = QRectF(self.rect()).center()
        anchor = center if anchor is None else anchor
        # Keep the image point beneath the cursor stationary.
        self._pan = anchor - center - (anchor - center - self._pan) * (new / old)
        self._fit, self._scale = False, new
        self.update_image()

    def paintEvent(self, event):
        if self.original.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        background = self.palette().color(QPalette.ColorRole.Window)
        painter.fillRect(self.rect(), background.darker(165))
        painter.setPen(background.lighter(150))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setClipRect(self.rect().adjusted(4, 4, -4, -4))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.image_scale < 1)
        size = self.original.size().toSizeF() * self.image_scale
        top_left = QRectF(self.rect()).center() + self._pan - QPointF(size.width(), size.height()) / 2
        painter.drawPixmap(QRectF(top_left, size), self.original, QRectF(self.original.rect()))
        region = self.selection.active
        if region:
            x0, y0, x1, y1 = region
            painter.setPen(QPen(Qt.GlobalColor.red, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(top_left + QPointF(x0, y0) * self.image_scale,
                                    top_left + QPointF(x1, y1) * self.image_scale))

    def image_point(self, position):
        size = self.original.size().toSizeF() * self.image_scale
        top_left = QRectF(self.rect()).center() + self._pan - QPointF(size.width(), size.height()) / 2
        return (position - top_left) / self.image_scale

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_image()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.zoom(1.2 ** max(-10, min(10, delta / 120)), event.position())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.selection.begin(self.image_point(event.position())):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.selection.anchor = None
            self._drag = None
            self.unsetCursor()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.fit_image()
            event.accept()
            return
        if not self.original.isNull() and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._scale = self.image_scale
            self._fit = False
            self._drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update_image()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.selection.move(self.image_point(event.position())):
            event.accept()
            return
        if self._drag is not None:
            self._pan += event.position() - self._drag
            self._drag = event.position()
            self.update_image()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.selection.end(self.image_point(event.position())):
            event.accept()
            return
        self._drag = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.selection.anchor = None
        self._drag = None
        self.unsetCursor()
        self.fit_image()
        event.accept()

    def keyPressEvent(self, event):
        action = {Qt.Key.Key_F: self.fit_image, Qt.Key.Key_1: self.actual_size,
                  Qt.Key.Key_Plus: lambda: self.zoom(1.2), Qt.Key.Key_Equal: lambda: self.zoom(1.2),
                  Qt.Key.Key_Minus: lambda: self.zoom(1 / 1.2)}.get(event.key())
        if action and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            action()
            event.accept()
        else:
            super().keyPressEvent(event)
