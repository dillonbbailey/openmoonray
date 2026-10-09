"""Link projection matrices to the live USD stage through the isolated worker."""
import copy
import uuid

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from .transform_graph import projection_matrix_mode


class TransformPicker(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Link USD Xform")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a prim by path or type…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.items = QListWidget()
        layout.addWidget(self.items)
        self.status = QLabel("Reading transforms from the current USD stage…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.choose = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.choose.setText("Link")
        self.choose.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.items.itemDoubleClicked.connect(lambda *_: self.accept())
        self.items.currentItemChanged.connect(self.filter_items)
        self.search.textChanged.connect(self.filter_items)
        layout.addWidget(buttons)

    def populate(self, entries, current):
        self.items.clear()
        for entry in entries:
            item = QListWidgetItem(entry["path"] + " · " + entry["type"])
            item.setData(Qt.ItemDataRole.UserRole, entry["path"])
            self.items.addItem(item)
            if entry["path"] == current:
                self.items.setCurrentItem(item)
        self.status.setText("Choose a prim. Its world transform will drive the projection matrix at the current frame."
                            if entries else "This stage contains no transformable prims.")
        self.filter_items()

    def filter_items(self, *_):
        query = self.search.text().strip().casefold()
        for i in range(self.items.count()):
            item = self.items.item(i)
            item.setHidden(query not in item.text().casefold())
        item = self.items.currentItem()
        self.choose.setEnabled(item is not None and not item.isHidden())

    def selected_path(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def accept(self):
        if self.choose.isEnabled():
            super().accept()


class TransformGraphController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.scene = window, None
        self.cache, self.failures, self.requests, self.waiters = {}, {}, {}, {}
        self.watched = set()
        self.picker = None
        window.usd_viewer.event_received.connect(self.receive_event)

    def paths(self):
        return {ref["path"] for doc in self.window.documents for node in doc.graph.data["nodes"]
                if (ref := node.get("usd_transform")) and ref["scene"] == self.scene and ref.get("follow", True)}

    def reconcile(self):
        paths = self.paths()
        if self.scene and paths != self.watched:
            self.watched = paths
            self.window.usd_viewer.send("watch_transform_graphs", scene=self.scene, paths=sorted(paths))

    def available(self):
        viewer = self.window.usd_viewer
        return bool(self.scene and viewer.ready and not viewer.loading and not viewer.edit_busy)

    def choose(self, document, node_id):
        if not self.available():
            return
        if self.picker:
            self.picker.reject()
        picker = self.picker = TransformPicker(self.window)
        scene = self.scene
        current = document.graph.node(node_id).get("usd_transform", {}).get("path", "")
        current = current or (self.window.usd_viewer.inspector.selection or {}).get("path", "")
        request = uuid.uuid4().hex
        self.requests[request] = ("choices", picker, current)
        def finished(result):
            self.requests.pop(request, None)
            if self.picker is picker:
                self.picker = None
            if result == QDialog.DialogCode.Accepted and scene == self.scene:
                self.link(document, node_id, picker.selected_path())
            picker.deleteLater()
        picker.finished.connect(finished)
        picker.open()
        self.window.usd_viewer.send("get_transform_choices", scene=scene, request=request)

    def link(self, document, node_id, path):
        if not self.available() or document not in self.window.documents:
            return
        request = uuid.uuid4().hex
        self.requests[request] = ("link", document, node_id)
        self.window.usd_viewer.send("get_transform_graph", scene=self.scene, path=path, request=request)

    def set_follow(self, document, node_id, enabled):
        self.window.apply("Follow USD transform" if enabled else "Freeze USD transform",
                          lambda g: g.node(node_id)["usd_transform"].update(follow=enabled), document=document)

    def unlink(self, document, node_id):
        self.window.apply("Unlink USD transform", lambda g: g.node(node_id).pop("usd_transform", None), document=document)

    def refresh_graph(self, graph):
        for node in graph.data["nodes"]:
            ref = node.get("usd_transform", {})
            info = self.cache.get(ref.get("path")) if ref.get("scene") == self.scene and ref.get("follow", True) else None
            if info:
                mode = projection_matrix_mode(graph.catalog, node["shader"])
                values = dict(info["values"], projection_mode=mode)
                if any(graph.value(node, key) != value for key, value in values.items()):
                    node["values"].update(copy.deepcopy(values))
                    ref["frame"] = info["reference"]["frame"]

    def receive_event(self, event):
        kind = event["event"]
        if kind in ("loaded", "scene_closed", "worker_stopped"):
            self.scene = None
            self.cache.clear()
            self.failures.clear()
            self.requests.clear()
            self.watched.clear()
            if self.picker:
                self.picker.reject()
            for request in list(self.waiters):
                self.complete(request, "The USD scene changed before transform refresh completed.")
        elif kind == "scene_cameras":
            self.scene = event["scene"]
            self.reconcile()
        elif kind in ("namespace_changed", "project_opened"):
            self.cache.clear()
            self.reconcile()
        elif kind == "transform_choices":
            record = self.requests.pop(event.get("request"), None)
            if record and event["scene"] == self.scene:
                _, picker, current = record
                picker.populate(event["prims"], current)
        elif kind == "transform_graph":
            record = self.requests.pop(event.get("request"), None)
            if not record or event["scene"] != self.scene:
                return
            _, doc, node_id = record
            if doc not in self.window.documents or not any(n["id"] == node_id for n in doc.graph.data["nodes"]):
                return
            info = event["transform"]
            self.cache[info["path"]] = info
            def change(graph):
                node = graph.node(node_id)
                previous = node.get("usd_transform", {})
                same_source = all(previous.get(key) == info["reference"][key] for key in ("scene", "path"))
                node["usd_transform"] = dict(info["reference"], follow=previous.get("follow", True) if same_source else True)
                graph.set_value(node_id, "projection_matrix", info["values"]["projection_matrix"])
                graph.set_value(node_id, "projection_mode", projection_matrix_mode(graph.catalog, node["shader"]))
            self.window.apply("Link USD transform", change, document=doc)
        elif kind == "transform_graphs_changed" and event["scene"] == self.scene:
            for info in event["transforms"]:
                path = info["path"]
                if "error" in info:
                    self.cache.pop(path, None)
                    self.failures[path] = info["error"]
                else:
                    self.failures.pop(path, None)
                    self.cache[path] = info
            for doc in self.window.documents:
                graph = doc.graph.clone()
                self.refresh_graph(graph)
                if graph.data != doc.graph.data:
                    graph.validate()
                    doc.undo.resetClean()
                    self.window.restore(graph.data, document=doc, derived=True)
            self.complete(event.get("request"))
            self.window.inspector_timer.start(0)
        elif kind == "error" and event.get("command") in ("get_transform_choices", "get_transform_graph", "refresh_transform_graphs"):
            record = self.requests.pop(event.get("request"), None)
            if record and record[0] == "choices":
                record[1].status.setText(event["message"])
            else:
                self.window.error(event["message"])
            self.complete(event.get("request"), event["message"])
        if kind in ("loaded", "scene_cameras", "scene_closed", "worker_stopped"):
            self.window.inspector_timer.start(0)

    def before_snapshot(self, ready, failed):
        paths = self.paths()
        if not self.scene or not paths:
            ready()
            return
        request = uuid.uuid4().hex
        self.waiters[request] = (ready, failed)
        self.watched = paths
        def read():
            if request not in self.waiters:
                return
            viewer = self.window.usd_viewer
            if viewer._time_in_flight is not None:
                QTimer.singleShot(20, self, read)
                return
            viewer.send("refresh_transform_graphs", scene=self.scene, paths=sorted(paths), request=request)
        read()
        QTimer.singleShot(10000, self, lambda: self.complete(request, "Timed out refreshing USD transforms."))

    def complete(self, request, error=None):
        callbacks = self.waiters.pop(request, None)
        if callbacks:
            ready, failed = callbacks
            failed(error) if error else ready()
