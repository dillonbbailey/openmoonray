"""Background .tx preparation; never changes scene values on completion."""
from collections import deque
import json
import os
from pathlib import Path
import signal
import tempfile

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import QLabel

from .maya_host import job_command
from .model import EDITOR_ROOT
from .tx_cache import cache_ready, plan_texture


class TextureConversions(QObject):
    updated = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.entries = {}
        self.queue = deque()
        self.process = self.job = None
        self.closed = False
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.kill_worker)

    def request(self, source, base=EDITOR_ROOT, *, retry=False):
        try:
            plan = plan_texture(source, base)
        except (OSError, ValueError) as exc:
            return dict(key="", state="error", detail=str(exc))
        key = plan["output"]
        entry = self.entries.get(key)
        if retry and entry and entry["state"] == "error":
            entry = None
        if entry and entry["state"] in {"queued", "converting", "error"}:
            return entry
        if cache_ready(plan):
            entry = dict(key=key, state="ready", detail=plan["output"], plan=plan)
        else:
            entry = dict(key=key, state="queued", detail="Waiting to prepare " + plan["source"], plan=plan)
            if not self.closed:
                self.queue.append(entry)
                QTimer.singleShot(0, self.start_next)
        self.entries[key] = entry
        return entry

    def start_next(self):
        if self.closed or self.process or not self.queue:
            return
        entry = self.queue.popleft()
        directory = tempfile.TemporaryDirectory(prefix="moonlab-tx-")
        request = Path(directory.name) / "request.json"
        request.write_text(json.dumps(dict(action="prepare_tx", input=entry["plan"]["source"],
                                          cache=str(Path(entry["plan"]["directory"]).parent))))
        self.job = (entry, directory)
        process = self.process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(lambda error: self.finished(-1) if error == QProcess.ProcessError.FailedToStart else None)
        entry.update(state="converting", detail="Preparing " + entry["plan"]["source"])
        self.updated.emit(entry["key"])
        command = job_command("render_view_worker") + [str(request)]
        process.start(command[0], command[1:])
        self.timeout.start(600000)

    def kill_worker(self):
        if self.process:
            pid = self.process.processId()
            if pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.kill()

    def finished(self, code, *_):
        if not self.process:
            return
        self.timeout.stop()
        process, self.process = self.process, None
        entry, directory = self.job
        self.job = None
        output = bytes(process.readAllStandardOutput()).decode(errors="replace")
        process.deleteLater()
        try:
            if code:
                raise ValueError(output.strip()[-2000:] or "Texture conversion failed or timed out.")
            result = json.loads((Path(directory.name) / "result.json").read_text())
            entry.update(state="ready", detail=result["texture"])
        except (OSError, ValueError, KeyError) as exc:
            entry.update(state="error", detail=str(exc))
        finally:
            directory.cleanup()
        if hasattr(self.parent(), "activity_log"):
            self.parent().activity_log.record("Materials", ".tx " + entry["state"] + ": " + entry["detail"])
        self.updated.emit(entry["key"])
        QTimer.singleShot(0, self.start_next)

    def close(self):
        self.closed = True
        self.queue.clear()
        self.timeout.stop()
        if self.process:
            self.process.finished.disconnect()
            self.process.errorOccurred.disconnect()
            self.kill_worker()
            self.process.waitForFinished(1000)
            self.process.deleteLater()
            self.process = None
            self.job[1].cleanup()
            self.job = None


class TextureConversionStatus(QLabel):
    def __init__(self, controller, entry):
        super().__init__()
        self.entry = entry
        self.setObjectName("tx_conversion_status")
        controller.updated.connect(self.refresh)
        self.refresh(entry["key"])

    def refresh(self, key):
        if key == self.entry["key"]:
            self.setText({"queued": ".tx queued", "converting": "Converting to .tx…",
                          "ready": ".tx ready", "error": ".tx failed"}[self.entry["state"]])
            self.setToolTip(self.entry["detail"])
