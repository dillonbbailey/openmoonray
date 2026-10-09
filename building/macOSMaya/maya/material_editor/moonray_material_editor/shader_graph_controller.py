"""Open USD shader networks in material tabs and send explicit apply requests."""
import copy
import uuid

from PySide6.QtCore import QObject

from .model import EDITOR_ROOT, Graph


class ShaderGraphController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.requests = {}
        self.applications = {}
        window.usd_viewer.edit_shader_requested.connect(self.open)
        window.usd_viewer.event_received.connect(self.receive_event)

    def focus(self, document, node):
        self.window.graph_tabs.setCurrentIndex(self.window.documents.index(document))
        self.window.switch_document(self.window.documents.index(document))
        document.canvas.select_node(node)
        document.canvas.fit_graph()
        self.window.show_material_editor()
        self.window.inspector_timer.start(0)

    def open(self, path):
        viewer = self.window.usd_viewer
        if not viewer.ready or viewer.loading or viewer.edit_busy:
            return
        owner = self.window.material_sync.owner(path)
        if owner:
            document, link = owner
            node = next((key for key, value in link["paths"].items() if value == path), document.graph.data["surface"])
            if node and any(n["id"] == node for n in document.graph.data["nodes"]):
                self.focus(document, node)
                return
        for document in self.window.documents:
            link = document.usd_shader_link
            if link and link["scene"] == viewer.path and link["frame"] == viewer.frame.value():
                nodes = {value: key for key, value in link["paths"].items()}
                if path == link["path"] or path in nodes:
                    selected = nodes.get(path) or document.graph.data["surface"] or document.selected
                    if selected and any(n["id"] == selected for n in document.graph.data["nodes"]):
                        self.focus(document, selected)
                        return
        if path in self.requests.values():
            return
        request = uuid.uuid4().hex
        self.requests[request] = path
        viewer.send("get_shader_graph", path=path, request=request)

    def available(self, document):
        viewer, link = self.window.usd_viewer, document.usd_shader_link
        return bool(link and viewer.ready and not viewer.loading and not viewer.edit_busy
                    and not self.applications and viewer._time_in_flight is None
                    and link["scene"] == viewer.path and link["frame"] == viewer.frame.value())

    def apply(self, document):
        if not self.window.commit_active_edits() or not self.available(document):
            return
        request = uuid.uuid4().hex
        self.applications[request] = document
        self.window.usd_viewer.edit_command("apply_shader_graph", request=request,
            graph=copy.deepcopy(document.graph.data), link=copy.deepcopy(document.usd_shader_link),
            base_dir=str(document.path.parent if document.path else EDITOR_ROOT))
        self.window.inspector_timer.start(0)

    def receive_event(self, event):
        kind, request = event["event"], event.get("request")
        if kind in ("loaded", "scene_closed", "worker_stopped"):
            self.requests.clear()
            self.applications.clear()
        elif kind == "shader_graph" and request in self.requests:
            self.requests.pop(request)
            if event["link"]["scene"] != self.window.usd_viewer.path:
                return
            try:
                # Two different shader requests can resolve to the same material.
                existing = next((d for d in self.window.documents if d.usd_shader_link and
                    all(d.usd_shader_link[key] == event["link"][key] for key in ("scene", "path", "frame"))), None)
                if existing:
                    path = event["link"]["paths"][event["selected"]]
                    node = next((k for k, v in existing.usd_shader_link["paths"].items() if v == path), existing.selected)
                    self.focus(existing, node)
                    return
                document = self.window.add_document(Graph(self.window.catalog, event["graph"]))
                if document:
                    document.usd_shader_link = event["link"]
                    self.focus(document, event["selected"])
            except (ValueError, KeyError) as exc:
                self.window.error(exc)
        elif kind == "shader_graph_applied" and request in self.applications:
            document = self.applications.pop(request)
            if document in self.window.documents and document.usd_shader_link:
                document.usd_shader_link = event["link"]
            self.window.statusBar().showMessage("Applied shader edits to " + event["link"]["path"], 10000)
        elif kind == "error" and event.get("command") in ("get_shader_graph", "apply_shader_graph"):
            self.requests.pop(request, None)
            self.applications.pop(request, None)
            self.window.error(event["message"])
        if kind in ("loaded", "scene_closed", "worker_stopped", "shader_graph_applied", "error", "time", "edit_state"):
            self.window.inspector_timer.start(0)
