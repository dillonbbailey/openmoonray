"""Debounce material edits and synchronize every associated USD material."""
import copy
import uuid

from PySide6.QtCore import QObject, QTimer

from .material_sync import signature, snapshot
from .model import EDITOR_ROOT


class MaterialSyncController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.records = {}
        self.pending = set()
        self.authored_pending = set()
        self.forced = set()
        self.running = None
        self.waiters = []
        self.discover = False
        self.preview_camera = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.flush)
        window.usd_viewer.sync_materials.toggled.connect(self.toggled)
        window.usd_viewer.event_received.connect(self.receive_event)

    def owner(self, path):
        for record in self.records.values():
            link = record["link"]
            if path == link["path"] or path in link["paths"].values():
                matches = [d for d in self.window.documents if d.graph.data["material_id"] == record["material_id"]]
                if len(matches) == 1:
                    return matches[0], link
        return None

    def changed(self, document, *, derived=False):
        self.pending.add(document.id)
        if not derived:
            self.authored_pending.add(document.id)
        if self.enabled():
            self.timer.start()

    def reconcile(self):
        documents = {d.id for d in self.window.documents}
        self.authored_pending.update(documents - self.pending)
        self.pending.update(documents)
        if self.enabled():
            self.timer.start()

    def toggled(self, enabled):
        if self.enabled():
            self.reconcile()
        else:
            self.timer.stop()
            if not self.running:
                self.complete_waiters()

    def before_snapshot(self, ready, failed):
        if self.window.usd_viewer.playing:
            self.window.usd_viewer.send("play", value=False)
        self.window.refresh_scene_links(
            lambda: self.window.light_graphs.before_snapshot(lambda: self.after_cameras(ready, failed), failed), failed)

    def set_auto_update(self, document, enabled):
        self.window.apply("Auto update USD material", lambda graph: graph.data.update(usd_auto_update=enabled),
                          render=False, document=document)

    def apply_now(self, document):
        if not self.window.commit_active_edits():
            return
        self.forced.add(document.id)
        self.changed(document)
        self.flush()

    def after_cameras(self, ready, failed):
        if not self.enabled() and not self.running:
            ready()
            return
        self.waiters.append((ready, failed))
        self.reconcile()
        self.flush()

    def enabled(self):
        return bool(self.forced) or self.window.usd_viewer.sync_materials.isChecked() or bool(self.preview_camera and self.preview_camera.get("locked"))

    def preview_update(self, document=None):
        from .preview_camera import snapshot as camera_snapshot, signature as camera_signature
        link = self.preview_camera
        if not link or not link.get("locked"):
            return None
        owners = [d for d in self.window.documents if d.graph.data["material_id"] == link["material_id"]]
        if len(owners) > 1:
            raise ValueError("More than one open tab owns the preview camera. Close the duplicate before synchronizing.")
        if not owners or document is not None and owners[0] is not document:
            return None
        owner = owners[0]
        data = camera_snapshot(owner.graph, owner.path.parent if owner.path else EDITOR_ROOT)
        return data if camera_signature(data) != link.get("signature") else None

    def complete_waiters(self, error=None):
        waiters, self.waiters = self.waiters, []
        for ready, failed in waiters:
            failed(error) if error else ready()

    def links(self, document):
        viewer = self.window.usd_viewer
        result = {path: record for path, record in self.records.items()
                  if record["material_id"] == document.graph.data["material_id"]}
        link = document.usd_shader_link
        if link and link["scene"] == viewer.path:
            result[link["path"]] = dict(material_id=document.graph.data["material_id"], link=link,
                                       signature=signature(link["baseline"]))
        return list(result.values())

    def flush(self):
        self.timer.stop()
        viewer = self.window.usd_viewer
        if self.running:
            return
        if not self.enabled():
            self.complete_waiters()
            return
        if not viewer.path or not viewer.ready or viewer.loading:
            self.complete_waiters("Open the associated USD scene before rendering.")
            return
        if viewer.edit_busy or viewer.playing or viewer._time_in_flight is not None:
            self.timer.start()
            return
        for document in self.window.documents:
            if document.id not in self.pending:
                continue
            self.pending.discard(document.id)
            derived = document.id not in self.authored_pending
            self.authored_pending.discard(document.id)
            manual = document.id in self.forced
            self.forced.discard(document.id)
            records = self.links(document) if manual or viewer.sync_materials.isChecked() and document.graph.data.get("usd_auto_update", True) else []
            try:
                camera = self.preview_update(document)
                if not records and not camera:
                    continue
                if sum(d.graph.data["material_id"] == document.graph.data["material_id"] for d in self.window.documents) > 1:
                    raise ValueError("More than one open tab owns this material. Close the duplicate before synchronizing.")
                base = str(document.path.parent if document.path else EDITOR_ROOT)
                graph = snapshot(document.graph, base) if records else None
                updates = []
                for record in records:
                    if signature(graph) == record["signature"]:
                        continue
                    link = copy.deepcopy(record["link"])
                    link.update(scene=viewer.path, frame=viewer.frame.value())
                    updates.append(dict(link=link, graph=graph, base_dir=base))
                if not updates and not camera:
                    continue
                request = uuid.uuid4().hex
                self.running = dict(request=request, document=document)
                viewer.edit_command("sync_materials", scene=viewer.path, frame=viewer.frame.value(),
                                    request=request, updates=updates, preview_camera=camera, derived=derived)
                return
            except (ValueError, KeyError) as exc:
                message = "Material sync: " + str(exc)
                self.window.error(message)
                self.complete_waiters(message)
        self.pending.intersection_update(d.id for d in self.window.documents)
        self.authored_pending.intersection_update(d.id for d in self.window.documents)
        self.forced.intersection_update(d.id for d in self.window.documents)
        if not self.pending:
            self.complete_waiters()

    def receive_event(self, event):
        kind = event["event"]
        if kind == "namespace_changed":
            self.namespace_refresh = True
        if kind in ("loaded", "scene_closed", "worker_stopped"):
            self.namespace_refresh = False
            self.timer.stop()
            self.records.clear()
            self.preview_camera = None
            self.pending.clear()
            self.authored_pending.clear()
            self.forced.clear()
            self.running = None
            self.discover = kind == "loaded"
            self.complete_waiters("The USD scene changed before material sync completed.")
        elif kind == "preview_camera" and event["scene"] == self.window.usd_viewer.path:
            changed = self.preview_camera != event["link"]
            self.preview_camera = event["link"]
            if changed:
                self.reconcile()
        elif kind == "scene_materials" and event["scene"] == self.window.usd_viewer.path:
            self.records = {r["link"]["path"]: r for r in event["records"]}
            if getattr(self, "namespace_refresh", False):
                for document in self.window.documents:
                    link = document.usd_shader_link
                    record = self.records.get(link["path"]) if link and link["scene"] == event["scene"] else None
                    if record:
                        document.usd_shader_link = record["link"]
                self.namespace_refresh = False
            if self.discover:
                self.discover = False
                self.reconcile()
        elif kind in ("material_bound", "project_opened"):
            self.reconcile()
        elif kind == "materials_synced" and self.running and event["request"] == self.running["request"]:
            document = self.running["document"]
            self.running = None
            for record in event["records"]:
                self.records[record["link"]["path"]] = record
                if document.usd_shader_link and document.usd_shader_link["path"] == record["link"]["path"]:
                    document.usd_shader_link = record["link"]
            self.window.statusBar().showMessage("Materials synchronized to USD", 4000)
            self.window.inspector_timer.start(0)
            self.timer.start(0)
        elif kind == "error" and event.get("command") == "sync_materials":
            self.running = None
            self.window.error("Material sync paused for this edit: " + event["message"])
            self.complete_waiters(event["message"])
            self.timer.start(0)
        elif kind == "shader_graph_applied":
            self.timer.start(0)
