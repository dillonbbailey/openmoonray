"""Shared, asynchronous texture thumbnail cache for graph nodes and inspectors."""
from collections import OrderedDict, deque
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile

from PySide6.QtCore import QObject, QProcess, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

from .maya_host import job_command
from .model import EDITOR_ROOT
from .log_text import clean_log_text
from .map_preview import IMAGE_SUFFIXES, map_snapshot, texture_path
from .themes import set_local_style


def texture_fields(graph, node):
    """Use catalog filename metadata, so any shader with an image input works."""
    for name, attr in graph.catalog.attributes(node["shader"]).items():
        if attr.get("filename"):
            value = graph.value(node, name)
            if isinstance(value, str) and value and Path(value).suffix.lower() in IMAGE_SUFFIXES:
                yield name, value, "normal" in (node["shader"] + name).lower()


class TexturePreviews(QObject):
    updated = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.cache = OrderedDict()
        self.queue = deque()
        self.map_queue = deque()
        self.map_owners = {}
        self.map_delay = QTimer(self)
        self.map_delay.setSingleShot(True)
        self.map_delay.setInterval(400)
        self.map_delay.timeout.connect(self.start_next)
        self.process = None
        self.job = None
        self.closed = False
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(20000)
        self.timeout.timeout.connect(self.timed_out)

    def request(self, value, base=EDITOR_ROOT, raw=False, size=256):
        size = max(1, min(2048, int(size)))
        path, prefix = texture_path(value, base)
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            error = "" if path.is_file() else "Texture is not a file"
        except OSError:
            stamp, error = None, "Texture not found"
        key = hashlib.sha256(repr((str(path), stamp, raw, size)).encode()).hexdigest()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        entry = dict(key=key, path=str(path), prefix=prefix, raw=raw, size=size, pixmap=QPixmap(),
                     status=error or "Loading texture…", tooltip=prefix + str(path))
        self.cache[key] = entry
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        if not error and not self.closed:
            # An explicitly selected texture takes priority over graph thumbnails.
            (self.queue.appendleft if size > 256 else self.queue.append)(entry)
            QTimer.singleShot(0, self.start_next)
        return entry

    def request_map(self, graph, node_id, base=EDITOR_ROOT):
        key, payload = map_snapshot(graph, node_id, base)
        key = "map:" + key
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        normal = graph.catalog.category(graph.node(node_id)["shader"]) == "NormalMap"
        entry = dict(key=key, payload=payload, pixmap=QPixmap(), ready=False, queued=False,
                     status="Rendering map…", tooltip="MoonRay output on a UV card · 128 × 128\n" +
                     ("Normals encoded as 0.5 × N + 0.5.\n" if normal else "") +
                     "Use the node's sRGB checkbox to switch between sRGB and Raw output previews." +
                     "\nUses connected inputs. Scene-dependent maps use the preview card's context.")
        self.cache[key] = entry
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return entry

    def set_maps(self, owner, entries):
        """Keep only the latest graph revisions queued, including across tabs."""
        self.map_owners[owner] = {entry["key"] for entry in entries}
        active = set().union(*self.map_owners.values())
        if self.job and "payload" in self.job[0] and self.job[0]["key"] not in active:
            self.job[0]["cancelled"] = True
            self.kill_worker()
        for entry in entries:
            if not entry["ready"] and not entry["queued"] and not (self.job and self.job[0] is entry):
                entry["queued"] = True
                self.map_queue.append(entry)
        if not self.closed:
            self.map_delay.start()

    def release_maps(self, owner):
        self.set_maps(owner, [])
        self.map_owners.pop(owner, None)

    def start_next(self):
        if self.closed or self.process:
            return
        entry = None
        if self.queue:
            entry = self.queue.popleft()
        elif not self.map_delay.isActive():
            active = set().union(*self.map_owners.values())
            while self.map_queue:
                candidate = self.map_queue.popleft()
                candidate["queued"] = False
                if candidate["key"] in active and not candidate["ready"]:
                    entry = candidate
                    break
        if entry is None:
            return
        directory = tempfile.TemporaryDirectory(prefix="lunatic-texture-")
        request = Path(directory.name) / "request.json"
        data = (dict(action="map_thumbnail", **entry["payload"]) if "payload" in entry else
                dict(action="thumbnail", input=entry["path"], raw=entry["raw"], size=entry["size"]))
        request.write_text(json.dumps(data))
        self.job = (entry, directory)
        process = self.process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(lambda error: self.finished(-1) if error == QProcess.ProcessError.FailedToStart else None)
        command = job_command("render_view_worker") + [str(request)]
        process.start(command[0], command[1:])
        self.timeout.start(60000 if "payload" in entry else 20000)

    def kill_worker(self):
        if self.process:
            pid = self.process.processId()
            if pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.kill()

    def timed_out(self):
        self.kill_worker()

    def finished(self, code, *_):
        if not self.process:
            return
        self.timeout.stop()
        process, self.process = self.process, None
        entry, directory = self.job
        self.job = None
        diagnostic = clean_log_text(bytes(process.readAllStandardOutput()).decode(errors="replace"))
        process.deleteLater()
        if entry.pop("cancelled", False):
            directory.cleanup()
            # Undo can make a cancelled revision current again before exit.
            if entry["key"] in set().union(*self.map_owners.values()):
                entry["queued"] = True
                self.map_queue.append(entry)
            QTimer.singleShot(0, self.start_next)
            return
        try:
            if code:
                raise ValueError(diagnostic.strip() or "Thumbnail decoding failed or timed out.")
            result = json.loads((Path(directory.name) / "result.json").read_text())
            image = QPixmap(result["image"])
            if image.isNull():
                raise ValueError("Could not load the texture preview.")
            if "payload" in entry:
                pixmaps = {mode: QPixmap(result["images"][mode]) for mode in ("srgb", "raw")}
                if any(pixmap.isNull() for pixmap in pixmaps.values()):
                    raise ValueError("Could not load the map's sRGB / Raw previews.")
                entry["pixmaps"] = pixmaps
            entry.update(pixmap=image, status=f"{result['width']} × {result['height']}")
            if "payload" not in entry:
                entry["tooltip"] += "\n" + entry["status"] + " · Source texture preview"
        except (OSError, ValueError, KeyError) as exc:
            entry["status"] = "Preview unavailable"
            entry["tooltip"] += "\n" + str(exc)[-2000:]
        finally:
            entry["ready"] = True
            directory.cleanup()
        self.updated.emit(entry["key"])
        QTimer.singleShot(0, self.start_next)

    def reset(self):
        self.close()
        self.cache.clear()
        self.closed = False

    def close(self):
        self.closed = True
        self.queue.clear()
        self.map_queue.clear()
        self.map_owners.clear()
        self.map_delay.stop()
        self.timeout.stop()
        if self.process:
            self.kill_worker()
            process, self.process = self.process, None
            process.finished.disconnect()
            process.errorOccurred.disconnect()
            process.kill()
            process.waitForFinished(1000)
            process.deleteLater()
            self.job[1].cleanup()
            self.job = None


class TexturePreviewLabel(QLabel):
    def __init__(self, cache, entry):
        super().__init__()
        self.entry = entry
        self.setObjectName("texture_thumbnail")
        self.setFixedHeight(120)
        self.setMinimumWidth(120)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_local_style(self, "background: #131e27; border: 1px solid #354653; border-radius: 4px; color: #8299aa;",
                        "border-radius: 4px;")
        cache.updated.connect(self.refresh)
        self.refresh(entry["key"])

    def refresh(self, key):
        if key != self.entry["key"]:
            return
        self.setToolTip(self.entry["tooltip"])
        image = self.entry["pixmap"]
        if image.isNull():
            self.setText(self.entry["status"])
        else:
            self.setPixmap(image.scaled(220, 110, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
