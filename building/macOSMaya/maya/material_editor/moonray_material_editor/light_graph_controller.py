"""Link the USD scene's lights to editable graph tabs and apply map textures."""
import json
import os
from pathlib import Path
import signal
import tempfile
import uuid

from PySide6.QtCore import QObject, QProcess, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTreeWidgetItem

from .maya_host import job_command
from .light_graph import light_snapshot
from .log_text import clean_log_text
from .model import CACHE_ROOT, EDITOR_ROOT, Graph
from .material_sync import signature


class LightGraphController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.scene = None
        self.revision = None
        self.lights = {}
        self.requests = set()
        self.process = None
        self.job = None
        self.applying = False
        self.application = None
        self.pending = {}
        self.applied = {}
        self.waiters = []
        self.auto_timer = QTimer(self)
        self.auto_timer.setSingleShot(True)
        self.auto_timer.setInterval(300)
        self.auto_timer.timeout.connect(self.flush)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(180000)
        self.timeout.timeout.connect(self.timed_out)
        self.group = QTreeWidgetItem(["USD SCENE LIGHTS"])
        self.group.setFlags(self.group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.group.setForeground(0, QColor("#8298a9"))
        window.scene_library.addTopLevelItem(self.group)
        self.group.setHidden(True)
        window.usd_viewer.event_received.connect(self.receive_event)
        window.usd_viewer.edit_light_requested.connect(self.open)

    def receive_event(self, event):
        kind = event["event"]
        if kind in ("loaded", "scene_closed", "worker_stopped"):
            self.shutdown()
            self.scene = None
            self.revision = None
            self.lights.clear()
            self.requests.clear()
            self.group.takeChildren()
            self.group.setHidden(True)
        elif kind == "scene_lights":
            self.scene, self.revision = event["scene"], event["revision"]
            self.lights = {item["path"]: item for item in event["lights"]}
            self.group.takeChildren()
            for item in event["lights"]:
                row = QTreeWidgetItem([item.get("name") or item["path"].rsplit("/", 1)[-1]])
                row.setData(0, Qt.ItemDataRole.UserRole, dict(usd_light=item["path"]))
                row.setToolTip(0, item["path"] + "\n" + item["shader"] + " · Double-click to edit this USD light in a graph.")
                self.group.addChild(row)
            self.window.filter_library(self.window.library_search.text())
            self.group.setExpanded(True)
            for document in self.window.documents:
                self.changed(document)
        elif kind == "light_graph" and event.get("request") in self.requests:
            self.requests.remove(event["request"])
            if event["scene"] != self.scene:
                return
            try:
                graph = Graph(self.window.catalog, event["graph"])
                doc = self.window.add_document(graph)
                if doc:
                    doc.canvas.select_node(graph.data["light"])
                    doc.canvas.fit_graph()
                    self.window.show_material_editor()
            except (ValueError, KeyError) as exc:
                self.window.error(exc)
        elif kind == "light_graph_applied":
            self.applying = False
            if self.application:
                self.applied[self.application["key"]] = self.application["signature"]
                self.application = None
            self.window.statusBar().showMessage("Applied light graph to " + event["path"], 10000)
            self.auto_timer.start()
        elif kind == "error" and event.get("command") in ("get_light_graph", "apply_light_graph"):
            self.requests.discard(event.get("request"))
            self.applying = False
            self.application = None
            self.window.error(event["message"])
            self.complete_waiters(event["message"])
            self.auto_timer.start()
        if kind in ("scene_lights", "loaded", "worker_stopped", "scene_closed", "light_graph_applied", "error"):
            self.window.inspector_timer.start(0)

    def open(self, path):
        if path not in self.lights:
            self.window.error("This light is unavailable. Open its USD scene first.")
            return
        for index, doc in enumerate(self.window.documents):
            for node in doc.graph.data["nodes"]:
                if node.get("usd_light") == dict(scene=self.scene, path=path):
                    self.window.graph_tabs.setCurrentIndex(index)
                    self.window.switch_document(index)
                    doc.canvas.select_node(node["id"])
                    self.window.show_material_editor()
                    return
        request = uuid.uuid4().hex
        self.requests.add(request)
        self.window.usd_viewer.send("get_light_graph", path=path, request=request)

    def available(self, node):
        reference = node.get("usd_light", {})
        viewer = self.window.usd_viewer
        return (not self.process and not self.applying and viewer.ready and not viewer.loading and not viewer.edit_busy
                and viewer._time_in_flight is None and reference.get("scene") == self.scene
                and reference.get("path") in self.lights)

    def set_auto_update(self, document, node_id, enabled):
        self.window.apply("Auto update USD light", lambda graph: graph.node(node_id).update(usd_auto_update=enabled),
                          render=False, document=document)

    def changed(self, document):
        for key in [key for key in self.pending if key[0] == document.id]:
            self.pending.pop(key)
        for node in document.graph.data["nodes"]:
            ref = node.get("usd_light", {})
            if node.get("usd_auto_update", False) and ref.get("scene") == self.scene and ref.get("path") in self.lights:
                self.pending[(document.id, node["id"])] = document
        if self.pending:
            self.auto_timer.start()

    def flush(self):
        if self.process or self.applying:
            return
        viewer = self.window.usd_viewer
        for key, document in list(self.pending.items()):
            if document not in self.window.documents:
                self.pending.pop(key)
                continue
            node = next((n for n in document.graph.data["nodes"] if n["id"] == key[1]), None)
            if not node or not node.get("usd_auto_update", False) or node.get("usd_light", {}).get("scene") != self.scene:
                self.pending.pop(key)
                continue
            if not viewer.ready or viewer.loading or node["usd_light"]["path"] not in self.lights:
                self.pending.pop(key)
                continue
            if not self.available(node) or viewer.playing:
                self.auto_timer.start()
                return
            self.pending.pop(key)
            try:
                current = signature(light_snapshot(document.graph, key[1], document.path.parent if document.path else EDITOR_ROOT).data)
                if self.applied.get(key) == current:
                    continue
                self.apply(document, key[1], automatic=True)
                return
            except (OSError, ValueError) as exc:
                self.window.error("USD light auto update: " + str(exc))
                self.complete_waiters(str(exc))
        self.complete_waiters()

    def before_snapshot(self, ready, failed):
        self.waiters.append((ready, failed))
        for document in self.window.documents:
            self.changed(document)
        self.flush()

    def complete_waiters(self, error=None):
        waiters, self.waiters = self.waiters, []
        for ready, failed in waiters:
            failed(error) if error else ready()

    def apply(self, document, node_id, *, automatic=False):
        if not automatic and not self.window.commit_active_edits():
            return
        node = document.graph.node(node_id)
        if not self.available(node):
            self.window.error("Open the referenced USD scene and wait for the current operation before applying the light.")
            return
        base = document.path.parent if document.path else EDITOR_ROOT
        try:
            graph = light_snapshot(document.graph, node_id, base)
            textures = CACHE_ROOT / "light-textures"
            directory = tempfile.TemporaryDirectory(prefix="lunatic-light-")
            request = Path(directory.name) / "request.json"
            request.write_text(json.dumps(dict(action="light_graph", graph=graph.data, node=node_id, textures=str(textures))))
        except (OSError, ValueError) as exc:
            self.window.error(exc)
            self.complete_waiters(str(exc))
            self.auto_timer.start()
            return
        self.job = dict(document=document, node=node_id, graph=graph.data, base=base, directory=directory,
                        scene=self.scene, revision=self.revision, frame=self.window.usd_viewer.frame.value(), automatic=automatic)
        process = self.process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(lambda error: self.finished(-1) if error == QProcess.ProcessError.FailedToStart else None)
        command = job_command("render_view_worker") + [str(request)]
        process.start(command[0], command[1:])
        self.timeout.start()
        self.window.statusBar().showMessage("Preparing light texture…")
        self.window.inspector_timer.start(0)

    def finished(self, code, *_):
        if not self.process:
            return
        process, self.process = self.process, None
        job, self.job = self.job, None
        self.timeout.stop()
        log = clean_log_text(bytes(process.readAllStandardOutput()).decode(errors="replace"))
        process.deleteLater()
        try:
            if code:
                raise ValueError("Could not prepare the light texture:\n" + log[-2000:])
            data = json.loads((Path(job["directory"].name) / "result.json").read_text())
            doc = job["document"]
            node = next((n for n in doc.graph.data["nodes"] if n["id"] == job["node"]), None)
            if job["automatic"] and (doc not in self.window.documents or not node or not node.get("usd_auto_update", False)):
                return
            if doc not in self.window.documents or signature(light_snapshot(doc.graph, job["node"], job["base"]).data) != signature(job["graph"]):
                if job["automatic"]:
                    self.changed(doc)
                    return
                raise ValueError("The light graph changed while preparing its texture. Apply again to use the latest values.")
            viewer = self.window.usd_viewer
            if (not self.available(doc.graph.node(job["node"])) or self.revision != job["revision"]
                    or self.scene != job["scene"] or viewer.frame.value() != job["frame"]):
                if job["automatic"]:
                    self.changed(doc)
                    return
                raise ValueError("The USD scene or frame changed while preparing the light. Apply again.")
            self.applying = True
            self.application = dict(key=(doc.id, job["node"]), signature=signature(job["graph"]))
            viewer.edit_command("apply_light_graph", **data, revision=job["revision"], frame=job["frame"])
        except (OSError, ValueError, KeyError) as exc:
            self.window.error(exc)
            self.complete_waiters(str(exc))
        finally:
            job["directory"].cleanup()
            self.window.inspector_timer.start(0)
            self.auto_timer.start()

    def timed_out(self):
        self.shutdown()
        self.window.error("Preparing the light texture timed out. The USD light was not changed.")
        self.window.inspector_timer.start(0)

    def shutdown(self):
        self.timeout.stop()
        self.auto_timer.stop()
        self.pending.clear()
        self.applied.clear()
        self.application = None
        self.complete_waiters("USD light update stopped.")
        self.applying = False
        if self.process:
            process, self.process = self.process, None
            process.finished.disconnect()
            process.errorOccurred.disconnect()
            if process.processId():
                try:
                    os.killpg(process.processId(), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill()
            process.waitForFinished(2000)
            process.deleteLater()
        if self.job:
            self.job["directory"].cleanup()
            self.job = None
