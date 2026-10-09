"""Asynchronous texture baking, with progress owned by the source graph tab."""
import json
import os
from pathlib import Path
import signal
import tempfile

from PySide6.QtCore import QObject, QProcess, QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout

from .maya_host import job_command
from .numeric_controls import NumericSpinBox

from .log_text import LogStream
from .map_preview import map_snapshot
from .model import EDITOR_ROOT
from .motion_bake import motion_options
from .render_progress_bar import logged_percent


class RampBakeDialog(QDialog):
    def __init__(self, path, parent=None, *, motion_blur=False):
        super().__init__(parent)
        self.setWindowTitle("Bake blur to texture" if motion_blur else "Bake to texture")
        self.motion_blur = motion_blur
        self.resize(560, 150)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        row = QHBoxLayout()
        self.filename = QLineEdit(str(path))
        self.filename.setObjectName("bake_filename")
        browse = QPushButton("…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.filename, 1)
        row.addWidget(browse)
        form.addRow("Filename", row)
        self.resolution = QSpinBox()
        self.resolution.setObjectName("bake_resolution")
        self.resolution.setRange(16, 8192)
        self.resolution.setValue(1024)
        self.resolution.setSuffix(" × " + str(1024))
        self.resolution.valueChanged.connect(lambda value: self.resolution.setSuffix(f" × {value}"))
        form.addRow("Resolution", self.resolution)
        if motion_blur:
            self.blur_form = form
            self.mode = QComboBox()
            self.mode.setObjectName("blur_type")
            self.mode.addItem("Rotational", "rotational")
            self.mode.addItem("Directional", "directional")
            form.addRow("Blur type", self.mode)
            self.angle = NumericSpinBox()
            self.angle.setObjectName("blur_angle")
            self.angle.setRange(0, 3600)
            self.angle.setValue(30)
            self.angle.setSuffix("°")
            self.angle.setToolTip("Total rotation during the shutter, centered on the current orientation. Zero is sharp; 360° is one rotation and 3600° is ten rotations.")
            self.rotation_row = form.rowCount()
            form.addRow("Blur angle", self.angle)
            center_row = QHBoxLayout()
            self.center = []
            for axis in ("U", "V"):
                center_row.addWidget(QLabel(axis))
                field = NumericSpinBox()
                field.setObjectName("blur_center_" + axis.lower())
                field.setRange(-10, 10)
                field.setSingleStep(.01)
                field.setValue(.5)
                field.setToolTip("Rotation pivot in UV coordinates; 0.5, 0.5 is the texture center.")
                center_row.addWidget(field, 1)
                self.center.append(field)
            self.center_row = form.rowCount()
            form.addRow("Center", center_row)
            self.direction = NumericSpinBox()
            self.direction.setObjectName("blur_direction")
            self.direction.setRange(0, 360)
            self.direction.setSuffix("°")
            self.direction.setToolTip("Any direction in UV space: 0° is horizontal, 90° is vertical, 45° is diagonal. The blur is centered on the original image.")
            self.direction_row = form.rowCount()
            form.addRow("Direction", self.direction)
            self.distance = NumericSpinBox()
            self.distance.setObjectName("blur_distance")
            self.distance.setRange(0, 1)
            self.distance.setSingleStep(.01)
            self.distance.setValue(.1)
            self.distance.setToolTip("Total travel in UV units, centered on the original image. Zero is sharp; 1 spans one texture width along the chosen direction.")
            self.distance_row = form.rowCount()
            form.addRow("Distance", self.distance)
            self.quality = QComboBox()
            self.quality.setObjectName("blur_quality")
            for title, samples in (("Draft · 64 samples/pixel", 8), ("Medium · 256 samples/pixel", 16),
                                   ("High · 1024 samples/pixel", 32)):
                self.quality.addItem(title, samples)
            self.quality.setCurrentIndex(1)
            self.quality.setToolTip("Higher quality reduces motion-blur grain and takes longer to bake.")
            form.addRow("Quality", self.quality)
            self.mode.currentIndexChanged.connect(self.update_blur_controls)
        layout.addLayout(form)
        hint = QLabel("Bakes this color map and its inputs over square 0–1 UVs. Saves a linear EXR and adds an ImageMap to this graph.")
        if motion_blur:
            hint.setText("Bakes this color map over square 0–1 UVs with motion blur. Saves a linear EXR and adds an ImageMap. Sampling beyond the image edges follows the source map's wrapping settings.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Bake")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if motion_blur:
            self.update_blur_controls()

    def update_blur_controls(self):
        rotational = self.mode.currentData() == "rotational"
        for row in (self.rotation_row, self.center_row):
            self.blur_form.setRowVisible(row, rotational)
        for row in (self.direction_row, self.distance_row):
            self.blur_form.setRowVisible(row, not rotational)

    def motion_options(self):
        if not self.motion_blur:
            return None
        result = dict(type=self.mode.currentData(), samples=self.quality.currentData())
        if result["type"] == "rotational":
            result.update(angle=self.angle.value(), center=[field.value() for field in self.center])
        else:
            result.update(direction=self.direction.value(), distance=self.distance.value())
        return result

    def browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Bake texture destination", self.filename.text(), "OpenEXR (*.exr)",
                                             options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.filename.setText(path)

    def accept(self):
        text = self.filename.text().strip()
        if not text:
            self.error.setText("Choose an EXR filename.")
            return
        target = Path(text).expanduser().absolute().with_suffix(".exr")
        if not target.parent.is_dir() or target.is_dir():
            self.error.setText("Choose a filename in an existing folder.")
            return
        if target.exists() and QMessageBox.question(self, "Replace texture?", f"Replace {target}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.filename.setText(str(target))
        super().accept()


class RampBakeController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.jobs = {}
        self.process = None
        self.active = None
        self.stream = LogStream()

    def state(self, job, status, percent=None):
        job["status"] = status
        if percent is not None:
            job["percent"] = percent
        doc, node = job["document"], job["node"]
        if doc in self.window.documents:
            doc.canvas.bake_states[node] = dict(status=status, percent=job["percent"])
            if node in doc.canvas.nodes:
                doc.canvas.nodes[node].update()

    def choose(self, document, node_id, *, motion_blur=False):
        if (document.id, node_id) in self.jobs or not self.window.commit_active_edits():
            return
        node = document.graph.node(node_id)
        if document.graph.catalog.category(node["shader"]) != "Map":
            return
        base = document.path.parent if document.path else EDITOR_ROOT
        suffix = "_motion_blur.exr" if motion_blur else "_baked.exr"
        dialog = RampBakeDialog(base / (document.graph.export_names()[node_id] + suffix), self.window, motion_blur=motion_blur)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.start(document, node_id, Path(dialog.filename.text()), dialog.resolution.value(), motion=dialog.motion_options())
        dialog.deleteLater()

    def start(self, document, node_id, destination, size, *, motion=None):
        key = (document.id, node_id)
        if key in self.jobs or document not in self.window.documents:
            return
        job = dict(key=key, document=document, node=node_id, destination=Path(destination), size=size, percent=0, motion=motion)
        self.jobs[key] = job
        self.state(job, "Preparing")

        def ready():
            if self.jobs.get(key) is not job or document not in self.window.documents:
                return
            try:
                graph = document.graph
                node = graph.node(node_id)
                if type(size) is not int or not 16 <= size <= 8192:
                    raise ValueError("Choose a resolution from 16 to 8192 pixels.")
                if graph.catalog.category(node["shader"]) != "Map":
                    raise ValueError("Choose a color map to bake.")
                if motion is not None:
                    job["motion"] = motion_options(motion)
                _, payload = map_snapshot(graph, node_id, document.path.parent if document.path else EDITOR_ROOT)
                if motion is not None:
                    payload["motion_blur"] = job["motion"]
                job.update(payload=payload, label=node["label"], position=list(node["position"]))
                self.state(job, "Queued")
                self.start_next()
            except (OSError, ValueError) as exc:
                failed(str(exc))

        def failed(message):
            if self.jobs.get(key) is job:
                self.jobs.pop(key)
                self.state(job, "Failed")
                self.window.error("Texture bake: " + message)

        self.window.refresh_scene_links(ready, failed)

    def start_next(self):
        if self.process:
            return
        for job in list(self.jobs.values()):
            if job["status"] != "Queued":
                continue
            doc = job["document"]
            if doc not in self.window.documents or not any(n["id"] == job["node"] for n in doc.graph.data["nodes"]):
                self.cancel(doc, job["node"])
                continue
            try:
                job["directory"] = tempfile.TemporaryDirectory(prefix="lunatic-ramp-bake-")
                request = Path(job["directory"].name) / "request.json"
                request.write_text(json.dumps(dict(action="bake_texture", **job["payload"], size=job["size"],
                                                  destination=str(job["destination"]))))
            except OSError as exc:
                self.jobs.pop(job["key"])
                if "directory" in job:
                    job["directory"].cleanup()
                self.state(job, "Failed")
                self.window.error(exc)
                continue
            self.active = job
            self.stream = LogStream()
            self.state(job, "Baking", 0)
            process = self.process = QProcess(self)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.readyReadStandardOutput.connect(self.read_output)
            process.finished.connect(self.finished)
            process.errorOccurred.connect(lambda error: self.finished(-1) if error == QProcess.ProcessError.FailedToStart else None)
            command = job_command("render_view_worker") + [str(request)]
            process.start(command[0], command[1:])
            self.window.statusBar().showMessage(f"Baking {job['label']} · {job['size']} × {job['size']}")
            return

    def read_output(self, final=False):
        if not self.process:
            return
        text = self.stream.feed(bytes(self.process.readAllStandardOutput()), final=final)
        if text:
            job = self.active
            self.state(job, "Baking", logged_percent(text, job["percent"]))
            doc = job["document"]
            doc.log = "\n".join((doc.log + "\n" + text).splitlines()[-2500:])
            if doc is self.window.document:
                self.window.log.appendPlainText(text)
            else:
                self.window.activity_log.record("Material · " + doc.graph.data["name"], text)

    def finished(self, code, *_):
        if not self.process:
            return
        self.read_output(final=True)
        process, self.process = self.process, None
        job, self.active = self.active, None
        self.jobs.pop(job["key"], None)
        process.deleteLater()
        doc = job["document"]
        try:
            if job.get("cancelled"):
                self.state(job, "Cancelled", 0)
                return
            if code:
                raise ValueError("MoonRay bake failed. See the material Render log.")
            result = json.loads((Path(job["directory"].name) / "result.json").read_text())
            path = Path(result["path"])
            if not path.is_file():
                raise ValueError("Baked texture is missing.")
            self.state(job, "Baked", 100)
            if doc not in self.window.documents:
                return
            created = []
            def add(graph):
                pos = [job["position"][0] + 300, job["position"][1]]
                # Include Scene outputs and full node bounds, not just their
                # origins. Place below occupied nodes in the adjacent column.
                from .canvas import NodeItem
                for item in doc.canvas.nodes.values():
                    bounds = item.sceneBoundingRect()
                    if bounds.left() < pos[0] + NodeItem.WIDTH + 18 and bounds.right() > pos[0] - 18:
                        pos[1] = max(pos[1], bounds.bottom() + 32)
                node = graph.add("ImageMap", pos)
                node["label"] = job["label"] + (" motion blur" if job["motion"] is not None else " baked")
                node["values"].update(texture=str(path), gamma=0)
                created.append(node["id"])
            self.window.apply("Add motion-blur texture" if job["motion"] is not None else "Add baked texture", add, render=False, document=doc)
            if created and doc is self.window.document:
                doc.canvas.select_node(created[0])
            self.window.statusBar().showMessage("Baked texture · " + str(path), 12000)
        except (OSError, ValueError, KeyError) as exc:
            self.state(job, "Failed")
            self.window.error(exc)
        finally:
            job["directory"].cleanup()
            QTimer.singleShot(0, self.start_next)

    def cancel(self, document, node_id):
        job = self.jobs.get((document.id, node_id))
        if not job:
            return
        job["cancelled"] = True
        if job is self.active and self.process:
            pid = self.process.processId()
            if pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.kill()
        else:
            self.jobs.pop(job["key"], None)
            self.state(job, "Cancelled", 0)

    def reconcile(self, document):
        nodes = {node["id"] for node in document.graph.data["nodes"]}
        for job in list(self.jobs.values()):
            if job["document"] is document and job["node"] not in nodes:
                self.cancel(document, job["node"])

    def close_document(self, document):
        for job in list(self.jobs.values()):
            if job["document"] is document:
                self.cancel(document, job["node"])
        if self.process and self.active["document"] is document:
            self.process.waitForFinished(2000)

    def shutdown(self):
        for job in list(self.jobs.values()):
            self.cancel(job["document"], job["node"])
        if self.process:
            self.process.waitForFinished(2000)
