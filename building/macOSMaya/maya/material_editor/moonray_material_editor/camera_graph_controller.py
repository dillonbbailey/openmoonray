"""Follow USD cameras in material graphs, without importing the native runtime."""
import copy
import uuid

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidgetItem


class CameraGraphController(QObject):
    controlled_values = {"node_xform", "film_width_aperture", "horizontal_film_offset", "vertical_film_offset",
                         "near", "far", "dof", "dof_aperture", "dof_focus_distance", "focal"}

    def __init__(self, window):
        super().__init__(window)
        self.window, self.scene = window, None
        self.cameras, self.requests = {}, {}
        self.cache, self.failures, self.waiters = {}, {}, {}
        self.watched = set()
        self.group = QTreeWidgetItem(["USD SCENE CAMERAS"])
        self.group.setFlags(self.group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.group.setForeground(0, QColor("#8298a9"))
        window.scene_library.addTopLevelItem(self.group)
        self.group.setHidden(True)
        window.usd_viewer.event_received.connect(self.receive_event)
        window.usd_viewer.add_camera_requested.connect(self.open)

    def receive_event(self, event):
        kind = event["event"]
        if kind in ("loaded", "scene_closed", "worker_stopped"):
            self.scene = None
            self.cameras.clear()
            self.requests.clear()
            self.cache.clear()
            self.failures.clear()
            self.watched.clear()
            for request in list(self.waiters):
                self.complete(request, "The USD scene changed before camera refresh completed.")
            self.group.takeChildren()
            self.group.setHidden(True)
        elif kind == "scene_cameras":
            self.scene = event["scene"]
            self.cameras = {item["path"]: item for item in event["cameras"]}
            self.group.takeChildren()
            for item in event["cameras"]:
                row = QTreeWidgetItem([item.get("name") or item["path"].rsplit("/", 1)[-1]])
                row.setData(0, Qt.ItemDataRole.UserRole, dict(usd_camera=item["path"]))
                row.setToolTip(0, item["path"] + "\nDouble-click to add a projector that follows this USD camera and the current frame.")
                self.group.addChild(row)
            self.window.filter_library(self.window.library_search.text())
            self.group.setExpanded(True)
            self.reconcile()
        elif kind == "project_opened":
            self.reconcile()
        elif kind == "namespace_changed":
            # Window remaps graph references before this controller receives the event.
            self.cache.clear()
            self.reconcile()
        elif kind == "camera_graphs_changed" and event["scene"] == self.scene:
            for info in event["cameras"]:
                path = info["path"]
                if "error" in info:
                    self.cache.pop(path, None)
                    self.failures[path] = info["error"]
                else:
                    self.failures.pop(path, None)
                    self.cache[path] = info
            self.update_documents()
            self.complete(event.get("request"))
            self.window.inspector_timer.start(0)
        elif kind == "camera_graph" and event.get("request") in self.requests:
            doc, node_id, position = self.requests.pop(event["request"])
            if doc not in self.window.documents or event["scene"] != self.scene:
                return
            info = event["camera"]
            self.cache[info["path"]] = info
            created = []
            def change(graph):
                node = graph.node(node_id) if node_id else graph.add(info["shader"], position)
                follow = node.get("usd_camera", {}).get("follow", True)
                node.update(shader=info["shader"], values=copy.deepcopy(info["values"]),
                            usd_camera=dict(info["reference"], follow=follow), ports=[])
                if not node_id:
                    node["label"] = info["name"]
                created.append(node["id"])
            self.window.apply("Refresh USD camera" if node_id else "Add USD camera", change, document=doc)
            if created:
                self.window.graph_tabs.setCurrentIndex(self.window.documents.index(doc))
                self.window.switch_document(self.window.documents.index(doc))
                doc.canvas.select_node(created[0])
                self.window.show_material_editor()
                self.window.statusBar().showMessage(
                    f"Camera captured at frame {info['reference']['frame']:g}. Connect out to a projection map's projector. Camera aspect: {info['aspect']:.4g}.", 15000)
        elif kind == "error" and event.get("command") == "get_camera_graph":
            self.requests.pop(event.get("request"), None)
            self.window.error(event["message"])
        elif kind == "error" and event.get("command") == "refresh_camera_graphs":
            self.complete(event.get("request"), event["message"])
        if kind in ("scene_cameras", "loaded", "scene_closed", "worker_stopped"):
            self.window.inspector_timer.start(0)

    def paths(self):
        return {ref["path"] for doc in self.window.documents for node in doc.graph.data["nodes"]
                if (ref := node.get("usd_camera")) and ref["scene"] == self.scene and ref.get("follow", True)}

    def reconcile(self):
        paths = self.paths()
        if self.scene and paths != self.watched:
            self.watched = paths
            self.window.usd_viewer.send("watch_camera_graphs", scene=self.scene, paths=sorted(paths))

    def refresh_graph(self, graph):
        """Reapply evaluated values after Undo/Redo without adding undo entries."""
        for node in graph.data["nodes"]:
            ref = node.get("usd_camera", {})
            info = self.cache.get(ref.get("path")) if ref.get("scene") == self.scene and ref.get("follow", True) else None
            if not info:
                continue
            attrs = graph.catalog.attributes(info["shader"])
            values = {key: value for key, value in node["values"].items() if key in attrs}
            values.update(copy.deepcopy(info["values"]))
            if node["shader"] != info["shader"] or node["values"] != values:
                node.update(shader=info["shader"], values=values)
                ref["frame"] = info["reference"]["frame"]
                node["ports"] = [port for port in node["ports"] if port in attrs]
                graph.data["connections"] = [c for c in graph.data["connections"]
                    if c["target"] != node["id"] or c["input"] in attrs]

    def update_documents(self):
        for doc in self.window.documents:
            graph = doc.graph.clone()
            self.refresh_graph(graph)
            if graph.data != doc.graph.data:
                graph.validate()
                doc.undo.resetClean()
                self.window.restore(graph.data, document=doc, derived=True)

    def set_follow(self, document, node_id, enabled):
        self.window.apply("Follow USD camera" if enabled else "Freeze USD camera",
                          lambda graph: graph.node(node_id)["usd_camera"].update(follow=enabled), document=document)

    def before_snapshot(self, ready, failed):
        """Read after queued USD edits, before serializing synchronized materials."""
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
            viewer.send("refresh_camera_graphs", scene=self.scene, paths=sorted(paths), request=request)
        read()
        QTimer.singleShot(10000, self, lambda: self.complete(request, "Timed out refreshing USD cameras."))

    def complete(self, request, error=None):
        callbacks = self.waiters.pop(request, None)
        if callbacks:
            ready, failed = callbacks
            failed(error) if error else ready()

    def available(self, node):
        ref = node.get("usd_camera", {})
        viewer = self.window.usd_viewer
        return (viewer.ready and not viewer.loading and not viewer.edit_busy and viewer._time_in_flight is None
                and ref.get("scene") == self.scene and ref.get("path") in self.cameras)

    def open(self, path, document=None, node_id=None):
        if not self.available(dict(usd_camera=dict(scene=self.scene, path=path))):
            self.window.error("Open the camera's USD scene and wait for the current operation first.")
            return
        doc = document or self.window.document
        if node_id is None:
            existing = next((n for n in doc.graph.data["nodes"] if n.get("usd_camera", {}).get("scene") == self.scene
                             and n["usd_camera"]["path"] == path), None)
            if existing:
                doc.canvas.select_node(existing["id"])
                self.window.show_material_editor()
                return
        center = doc.canvas.mapToScene(doc.canvas.viewport().rect().center())
        position = self.window.add_position or [center.x() - 122, center.y() - 80]
        self.window.add_position = None
        request = uuid.uuid4().hex
        self.requests[request] = (doc, node_id, position)
        self.window.usd_viewer.send("get_camera_graph", path=path, request=request)
