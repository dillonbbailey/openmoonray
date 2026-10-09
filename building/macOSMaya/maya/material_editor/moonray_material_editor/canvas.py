"""Qt Graphics View node canvas. Graph mutations belong to the document owner."""
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QKeySequence, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QCheckBox, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsProxyWidget, QGraphicsScene, QGraphicsView, QLineEdit, QMenu
from .model import TERMINALS, EDITOR_ROOT
from .texture_previews import texture_fields
from .graph_layout import layout_nodes
from .themes import set_local_style
from .number_format import compact_number

COLORS = {"Map": "#7fcbb5", "NormalMap": "#bda0e0", "Material": "#e5b97d", "Displacement": "#ef9fa2", "Output": "#8fb9ef", "Volume": "#9aafd8", "Light": "#eed084", "LightFilter": "#deb38c", "DisplayFilter": "#ba9fd8", "RenderOutput": "#97aed9", "Geometry": "#88bbc9", "Camera": "#88bbc9", "LightSet": "#eed084"}


def curve(a, b):
    path = QPainterPath(a)
    d = max(70, abs(b.x() - a.x()) * 0.5)
    path.cubicTo(a + QPointF(d, 0), b - QPointF(d, 0), b)
    return path


class Port(QGraphicsEllipseItem):
    def __init__(self, node_id, name, output, color, parent, x, y):
        super().__init__(-7, -7, 14, 14, parent)
        self.node_id, self.name, self.output = node_id, name, output
        self.setPos(x, y)
        self.setBrush(QColor(color))
        self.setPen(QPen(QColor("#172129"), 2))
        self.setZValue(5)
        self.setToolTip("Drag to connect" if output else f"{name} · drag to connect / right-click to disconnect")


class InstanceNameEdit(QLineEdit):
    """Single-line instance naming inside the node, using the document's undo."""
    def __init__(self, node, view):
        super().__init__(node["label"])
        self.node_id, self.view, self.saved_name = node["id"], view, node["label"]
        self.setObjectName("instance_name_" + self.node_id)
        self.setAccessibleName("Instance name")
        self.setToolTip("Instance name · used for export\nEnter or click away to save · Escape to cancel")
        set_local_style(self, "QLineEdit { font-size: 12px; padding: 4px 7px; color: #edf3f7; "
                          "background: #141f28; border: 1px solid #435663; border-radius: 4px; } "
                          "QLineEdit:focus { border-color: #7fcbb5; }",
                          "QLineEdit { font-size: 12px; padding: 4px 7px; border-radius: 4px; }")
        self.editingFinished.connect(self.commit)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.view.select_node(self.node_id)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.setText(self.saved_name)
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)

    def commit(self):
        if self.view.loading:
            return
        name = self.text().strip() or self.saved_name
        self.setText(name)
        if name != self.saved_name:
            self.saved_name = name
            # The receiver is queued: restoring the document rebuilds this scene,
            # so it must happen after Qt finishes dispatching the field's event.
            self.view.name_edited.emit(self.node_id, name)


class NodeItem(QGraphicsItem):
    WIDTH = 244

    def __init__(self, node, graph, view, output=False):
        super().__init__()
        self.node, self.graph, self.view, self.output_node = node, graph, view, output
        self.node_id = node["id"]
        self.category = "Output" if output else graph.catalog.category(node["shader"])
        self.motion_bake = self.category == "Map"
        self.texture_stride = 116 if self.motion_bake else 104
        self.color = COLORS.get(self.category, "#90bac9")
        self.port_names = list(TERMINALS) if output else node["ports"]
        self.output_names = [] if output else graph.catalog.outputs(node["shader"])
        self.header_offset = 0 if output else 64
        self.height = 112 + self.header_offset + 28 * max(len(self.port_names), len(self.output_names))
        self.texture_top = self.height - 24
        self.textures = []
        self.srgb_checkbox = None
        if not output and view.texture_previews:
            for name, value, raw in texture_fields(graph, node):
                entry = view.texture_previews.request(value, view.texture_base(), raw)
                self.textures.append((name, entry))
            if self.category in {"Map", "NormalMap"}:
                entry = view.texture_previews.request_map(graph, self.node_id, view.texture_base())
                self.textures.append(("Output", entry))
            self.height += math.ceil(len(self.textures) / 2) * self.texture_stride
        self.ports = {}
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setPos(*node["position"])
        if not output:
            self.setToolTip(node["shader"] + ("\n\n" + "\n\n".join(
                name + "\n" + entry["tooltip"] for name, entry in self.textures) if self.textures else ""))
            self.name_edit = InstanceNameEdit(node, view)
            self.name_proxy = QGraphicsProxyWidget(self)
            self.name_proxy.setWidget(self.name_edit)
            self.name_proxy.setGeometry(QRectF(14, 28, self.WIDTH - 28, 30))
        if self.category in {"Map", "NormalMap"} and view.texture_previews:
            self.srgb_checkbox = QCheckBox("sRGB")
            self.srgb_checkbox.setObjectName("preview_srgb_" + self.node_id)
            self.srgb_checkbox.setAccessibleName("sRGB output preview")
            self.srgb_checkbox.setToolTip("Output preview: checked = sRGB, unchecked = Raw.\n"
                                         "Changes only this thumbnail's display, not the shader or its texture input.")
            set_local_style(self.srgb_checkbox, "QCheckBox { background: transparent; color: #b6c7d2; "
                            "font-size: 11px; spacing: 5px; }",
                            "QCheckBox { background: transparent; font-size: 11px; spacing: 5px; }")
            self.srgb_checkbox.setChecked(node.get("preview_srgb", self.category != "NormalMap"))
            self.srgb_proxy = QGraphicsProxyWidget(self)
            self.srgb_proxy.setWidget(self.srgb_checkbox)
            self.srgb_proxy.setGeometry(QRectF(self.WIDTH - 84, self.height - 26, 70, 22))
            self.srgb_checkbox.toggled.connect(self.set_preview_display)
        for i, name in enumerate(self.port_names):
            attr = {} if output else graph.catalog.attributes(node["shader"])[name]
            kind = TERMINALS[name] if output else graph.catalog.input_kind(attr)
            color = COLORS.get(kind, COLORS["Material"])
            self.ports[name] = Port(node["id"], name, False, color, self, 0, self.header_offset + 86 + i * 28)
        if not output:
            self.out_ports = {name: Port(node["id"], name, True, self.color, self, self.WIDTH, self.header_offset + 54 + i * 28) for i, name in enumerate(self.output_names)}
            self.out_port = self.out_ports["out"]

    def boundingRect(self):
        return QRectF(-9, -2, self.WIDTH + 18, self.height + 4)

    def set_preview_display(self, srgb):
        self.update()
        if not self.view.loading:
            # Like inline naming, the document rebuild must follow this event.
            self.view.preview_display_edited.emit(self.node_id, srgb)

    def preview_image(self, entry):
        mode = "srgb" if self.srgb_checkbox and self.srgb_checkbox.isChecked() else "raw"
        return entry.get("pixmaps", {}).get(mode, entry["pixmap"])

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.WIDTH, self.height)
        painter.setPen(QPen(QColor(self.color if self.isSelected() else "#36424d"), 1.7 if self.isSelected() else 1))
        painter.setBrush(QColor("#202a34"))
        painter.drawRoundedRect(rect, 10, 10)
        if not self.output_node:
            painter.setFont(QFont("DejaVu Sans", 7))
            painter.setPen(QColor("#92a3b1"))
            painter.drawText(QRectF(16, 8, 212, 17), Qt.AlignmentFlag.AlignVCenter, "INSTANCE NAME")
            painter.setPen(QPen(QColor("#36424d"), 1))
            painter.drawLine(QPointF(14, 66), QPointF(self.WIDTH - 14, 66))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.color))
        painter.drawRoundedRect(QRectF(13, self.header_offset + 15, 5, 20), 2, 2)
        painter.setPen(QColor("#edf3f7"))
        font = QFont("DejaVu Sans", 10)
        font.setBold(True)
        painter.setFont(font)
        title = self.node["label"] if self.output_node else self.node["shader"]
        painter.drawText(QRectF(28, self.header_offset + 10, 202, 28), Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, 196))
        painter.setFont(QFont("DejaVu Sans", 8))
        painter.setPen(QColor("#92a3b1"))
        if self.output_node:
            painter.drawText(QRectF(16, 41, 213, 22), Qt.AlignmentFlag.AlignVCenter, "SCENE OUTPUTS")
        output_left = self.WIDTH - 41
        input_right = output_left - 8 if self.output_names else self.WIDTH - 16
        metrics = painter.fontMetrics()
        for i, name in enumerate(self.port_names):
            row_top = self.header_offset + 75 + i * 28
            label_right = input_right
            if not self.output_node:
                attr = self.graph.catalog.attributes(self.node["shader"])[name]
                if not self.graph.incoming(self.node_id, name) and attr["attrType"] in ("Float", "Double"):
                    value = self.graph.value(self.node, name)
                    text = compact_number(value)
                    width = min(96, max(64, metrics.horizontalAdvance(text) + 4))
                    value_rect = QRectF(input_right - width, row_top, width, 22)
                    label_right = value_rect.left() - 8
                    painter.setPen(QColor("#718796"))
                    painter.drawText(value_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            painter.setPen(QColor("#c3cfd7"))
            label_rect = QRectF(16, row_top, label_right - 16, 22)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(name.replace("_", " "), Qt.TextElideMode.ElideRight, int(label_rect.width())))
        painter.setPen(QColor(self.color))
        for i, name in enumerate(self.output_names):
            painter.drawText(QRectF(output_left, self.header_offset + 43 + i * 28, 28, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "rgb" if name == "out" and self.category == "Map" else name)
        for index, (name, entry) in enumerate(self.textures):
            box = QRectF(14 + (index % 2) * 112, self.texture_top + (index // 2) * self.texture_stride, 104, 78)
            painter.setPen(QPen(QColor("#354653"), 1))
            painter.setBrush(QColor("#131e27"))
            painter.drawRoundedRect(box, 4, 4)
            image = self.preview_image(entry)
            if image.isNull():
                painter.setPen(QColor("#8299aa"))
                painter.drawText(box.adjusted(4, 2, -4, -2), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, entry["status"])
            else:
                size = image.size().scaled(100, 74, Qt.AspectRatioMode.KeepAspectRatio)
                target = QRectF(box.center().x() - size.width() / 2, box.center().y() - size.height() / 2, size.width(), size.height())
                painter.drawPixmap(target, image, QRectF(image.rect()))
            painter.setPen(QColor("#92a3b1"))
            caption = name
            if "payload" in entry:
                caption += " · " + ("sRGB" if self.srgb_checkbox.isChecked() else "Raw")
            bake = self.view.bake_states.get(self.node_id) if name == "Output" else None
            if bake:
                bar = QRectF(box.left(), box.bottom() + 3, box.width(), 4)
                painter.fillRect(bar, QColor("#354653"))
                painter.fillRect(QRectF(bar.left(), bar.top(), bar.width() * bake["percent"] / 100, bar.height()),
                                 QColor("#dd7777" if bake["status"] == "Failed" else "#61b8dc"))
                caption = bake["status"] + (f" · {bake['percent']}%" if bake["status"] in ("Baking", "Baked") else "")
            painter.drawText(QRectF(box.left(), box.bottom() + (10 if self.motion_bake else 2), box.width(), 20), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(caption.replace("_", " "), Qt.TextElideMode.ElideRight, 104))
        footer = "Preview / export" if self.output_node else self.category.upper()
        painter.drawText(QRectF(16, self.height - 24, 200, 18), Qt.AlignmentFlag.AlignVCenter, footer)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.view.update_edges()
        return super().itemChange(change, value)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, source, target, connection, color):
        super().__init__()
        self.source, self.target, self.connection = source, target, connection
        self.color = color
        self.setZValue(-1)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.update_path()

    def update_path(self):
        self.setPath(curve(self.source.scenePos(), self.target.scenePos()))
        self.setPen(QPen(QColor(self.color), 2.3))

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(14)
        return stroker.createStroke(self.path())

    def paint(self, painter, option, widget=None):
        self.setPen(QPen(QColor("#ffffff" if self.isSelected() else self.color), 3 if self.isSelected() else 2.3))
        super().paint(painter, option, widget)


class GraphView(QGraphicsView):
    selection = Signal(object)
    connect_requested = Signal(str, str, str, str)
    disconnect_requested = Signal(str, str)
    delete_requested = Signal(object, object)
    copy_requested = Signal(object)
    paste_requested = Signal(object)
    moved = Signal(object)
    add_requested = Signal(object)
    rename_requested = Signal(str)
    name_edited = Signal(str, str)
    preview_display_edited = Signal(str, bool)
    relayout_requested = Signal()
    bake_requested = Signal(str)
    motion_bake_requested = Signal(str)
    cancel_bake_requested = Signal(str)
    view_usda_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setSceneRect(-6000, -4000, 12000, 8000)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setBackgroundBrush(QColor("#151d25"))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nodes, self.edges = {}, []
        self.drag_port = self.drag_wire = self.pan_start = None
        self.usd_reference = lambda node_id: None
        self.loading = False
        self.texture_previews = None
        self.texture_base = lambda: EDITOR_ROOT
        self.bake_states = {}
        self.scene().selectionChanged.connect(self._selection)

    def texture_ready(self, key):
        for item in self.nodes.values():
            if any(entry["key"] == key for _, entry in item.textures):
                item.setToolTip(item.node["shader"] + "\n\n" + "\n\n".join(
                    name + "\n" + entry["tooltip"] for name, entry in item.textures))
                item.update()

    def _selection(self):
        if not self.loading:
            items = [i for i in self.scene().selectedItems() if isinstance(i, NodeItem) and not i.output_node]
            self.selection.emit(items[0].node_id if items else None)

    def load(self, graph):
        selected = {i.node_id for i in self.scene().selectedItems() if isinstance(i, NodeItem)}
        self.loading = True
        self.nodes, self.edges = {}, []
        self.drag_port = self.drag_wire = None
        self.scene().clear()
        for node in graph.data["nodes"]:
            item = NodeItem(node, graph, self)
            self.scene().addItem(item)
            self.nodes[node["id"]] = item
            item.setSelected(node["id"] in selected)
        item = NodeItem({"id": "__output__", "label": graph.data["name"], "position": [365, 40]}, graph, self, True)
        item.setToolTip("Scene outputs · " + graph.data["name"])
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.nodes["__output__"] = item
        self.scene().addItem(item)
        for c in graph.data["connections"]:
            self._edge(c["source"], c["target"], c["input"], c)
        for terminal in TERMINALS:
            if graph.data[terminal]:
                self._edge(graph.data[terminal], "__output__", terminal, {"target": "__output__", "input": terminal})
        if self.texture_previews:
            self.texture_previews.set_maps(id(self), [entry for item in self.nodes.values()
                                                     for _, entry in item.textures if "payload" in entry])
        self.setSceneRect(QRectF(-6000, -4000, 12000, 8000).united(
            self.scene().itemsBoundingRect().adjusted(-300, -300, 300, 300)))
        self.loading = False

    def _edge(self, source, target, port, connection):
        src, dst = self.nodes[source], self.nodes[target]
        edge = EdgeItem(src.out_ports[connection.get("output", "out")], dst.ports[port], connection, src.color)
        self.scene().addItem(edge)
        self.edges.append(edge)

    def update_edges(self):
        for edge in self.edges:
            edge.update_path()

    def select_node(self, node_id):
        self.select_nodes([node_id])

    def select_nodes(self, node_ids):
        self.loading = True
        self.scene().clearSelection()
        for node_id in node_ids:
            if node_id in self.nodes:
                self.nodes[node_id].setSelected(True)
        self.loading = False
        self._selection()

    def cursor_scene_position(self):
        point = self.viewport().mapFromGlobal(QCursor.pos())
        if not self.viewport().rect().contains(point):
            point = self.viewport().rect().center()
        return self.mapToScene(point)

    def fit_graph(self):
        rect = self.scene().itemsBoundingRect().adjusted(-70, -80, 70, 80)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.2:
            self.scale(1.2 / self.transform().m11(), 1.2 / self.transform().m11())

    def layout_positions(self):
        sizes = {node: (item.boundingRect().width(), item.boundingRect().height())
                 for node, item in self.nodes.items()}
        edges = [(edge.source.node_id, edge.target.node_id, edge.source.pos().y(), edge.target.pos().y())
                 for edge in self.edges]
        output = self.nodes["__output__"].pos()
        return layout_nodes(sizes, edges, origin=(output.x(), output.y()))

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        step = 24 if self.transform().m11() >= 0.5 else 96
        painter.setPen(QPen(QColor("#2a3742"), 1))
        left, top = math.floor(rect.left() / step) * step, math.floor(rect.top() / step) * step
        points = [QPointF(x, y) for x in range(left, math.ceil(rect.right()), step) for y in range(top, math.ceil(rect.bottom()), step)]
        if points:
            painter.drawPoints(points)

    def wheelEvent(self, event):
        factor = 1.14 if event.angleDelta().y() > 0 else 1 / 1.14
        if 0.16 < self.transform().m11() * factor < 2.5:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and isinstance(item, Port):
            self.drag_port = item
            self.drag_wire = QGraphicsPathItem()
            self.drag_wire.setPen(QPen(QColor("#e2f2f0"), 2, Qt.PenStyle.DashLine))
            self.drag_wire.setZValue(10)
            self.scene().addItem(self.drag_wire)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pan_start is not None:
            pos = event.position().toPoint()
            delta = pos - self.pan_start
            self.pan_start = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        elif self.drag_port:
            a, b = self.drag_port.scenePos(), self.mapToScene(event.position().toPoint())
            self.drag_wire.setPath(curve(a, b) if self.drag_port.output else curve(b, a))
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self.pan_start is not None:
            self.pan_start = None
            self.unsetCursor()
            return
        if self.drag_port:
            self.scene().removeItem(self.drag_wire)
            destination = self.itemAt(event.position().toPoint())
            origin = self.drag_port
            self.drag_wire = self.drag_port = None
            if isinstance(destination, Port) and destination.output != origin.output:
                src, dst = (origin, destination) if origin.output else (destination, origin)
                self.connect_requested.emit(src.node_id, dst.node_id, dst.name, src.name)
            return
        super().mouseReleaseEvent(event)
        positions = {n: [i.pos().x(), i.pos().y()] for n, i in self.nodes.items() if n != "__output__"}
        self.moved.emit(positions)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if isinstance(item, QGraphicsProxyWidget):
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        menu.setObjectName("shader_graph_context_menu")
        if isinstance(item, Port) and not item.output:
            menu.addAction("Disconnect input", lambda: self.disconnect_requested.emit(item.node_id, item.name))
        elif isinstance(item, EdgeItem):
            menu.addAction("Disconnect", lambda: self.delete_requested.emit([], [item.connection]))
        elif isinstance(item, NodeItem):
            if not item.output_node:
                menu.addAction("Rename node…", lambda: self.rename_requested.emit(item.node_id))
            if self.usd_reference(item.node_id):
                menu.addAction("View as USDA", lambda: self.view_usda_requested.emit(item.node_id))
            if item.motion_bake:
                status = self.bake_states.get(item.node_id, {}).get("status")
                if status in ("Preparing", "Queued", "Baking"):
                    menu.addAction("Cancel bake", lambda: self.cancel_bake_requested.emit(item.node_id))
                else:
                    bake = menu.addMenu("Bake")
                    bake.addAction("to texture", lambda: self.bake_requested.emit(item.node_id))
                    bake.addAction("blur to texture", lambda: self.motion_bake_requested.emit(item.node_id))
        else:
            point = self.mapToScene(event.pos())
            menu.addAction("Add shader…", lambda: self.add_requested.emit(point))
        menu.addSeparator()
        menu.addAction("Relayout nodes", self.relayout_requested.emit)
        menu.addAction("Frame graph", self.fit_graph)
        menu.exec(event.globalPos())

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and isinstance(item, NodeItem) and not item.output_node:
            self.rename_requested.emit(item.node_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def event(self, event):
        from .shortcuts import matches_shortcut
        # QWidget normally consumes Tab for focus traversal before keyPressEvent.
        # Keep that behavior for embedded fields, but use Tab on the graph to add.
        if (event.type() == QEvent.Type.KeyPress and matches_shortcut(event, "graph.library") and not isinstance(self.scene().focusItem(), QGraphicsProxyWidget)):
            if not event.isAutoRepeat() and self.drag_port is None and self.pan_start is None:
                self.add_requested.emit(self.cursor_scene_position())
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        from .shortcuts import matches_shortcut
        if isinstance(self.scene().focusItem(), QGraphicsProxyWidget):
            super().keyPressEvent(event)
            return
        if matches_shortcut(event, "graph.copy"):
            if not event.isAutoRepeat() and self.drag_port is None and self.pan_start is None:
                nodes = [i.node_id for i in self.scene().selectedItems() if isinstance(i, NodeItem) and not i.output_node]
                self.copy_requested.emit(nodes)
            event.accept()
        elif matches_shortcut(event, "graph.paste"):
            if not event.isAutoRepeat() and self.drag_port is None and self.pan_start is None:
                self.paste_requested.emit(self.cursor_scene_position())
            event.accept()
        elif matches_shortcut(event, "graph.rename"):
            nodes = [i for i in self.scene().selectedItems() if isinstance(i, NodeItem) and not i.output_node]
            if len(nodes) == 1:
                self.rename_requested.emit(nodes[0].node_id)
        elif matches_shortcut(event, "graph.delete"):
            nodes = [i.node_id for i in self.scene().selectedItems() if isinstance(i, NodeItem) and not i.output_node]
            edges = [i.connection for i in self.scene().selectedItems() if isinstance(i, EdgeItem)]
            self.delete_requested.emit(nodes, edges)
        elif matches_shortcut(event, "graph.frame"):
            self.fit_graph()
        else:
            super().keyPressEvent(event)
