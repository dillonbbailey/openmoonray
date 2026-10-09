"""Persistent File-menu history for projects, USD stages and material graphs."""
import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMenu, QMessageBox


HISTORY_KEY = "recentScenes/files"
HISTORY_LIMIT = 10
KINDS = {"project": "Project", "usd": "USD", "graph": "Graph"}


def settings_store():
    # Maya: its own settings, not shared with MoonLab's Lunatic application.
    return QSettings("OpenMoonRay", "MoonrayMayaMaterialEditor")


class RecentScenes(QMenu):
    def __init__(self, window):
        super().__init__("Recent Scenes", window)
        self.window = window
        self.store = settings_store()
        self.setToolTipsVisible(True)
        self.aboutToShow.connect(self.rebuild)
        window.usd_viewer.event_received.connect(self.viewer_event)
        self.rebuild()

    def entries(self):
        self.store.sync()
        try:
            records = json.loads(self.store.value(HISTORY_KEY, "[]"))
        except (ValueError, TypeError):
            return []
        if not isinstance(records, list):
            return []
        result, paths = [], set()
        for record in records:
            if not isinstance(record, dict):
                continue
            path, kind = record.get("path"), record.get("kind")
            if not isinstance(kind, str) or kind not in KINDS or not isinstance(path, str) or not Path(path).is_absolute():
                continue
            if path not in paths:
                result.append(dict(path=path, kind=kind))
                paths.add(path)
            if len(result) == HISTORY_LIMIT:
                break
        return result

    def write_entries(self, entries):
        self.store.setValue(HISTORY_KEY, json.dumps(entries))
        self.store.sync()

    def remember(self, kind, path):
        path = Path(path).expanduser().resolve()
        if kind not in KINDS or not path.is_file():
            return
        entry = dict(kind=kind, path=str(path))
        self.write_entries(([entry] + [row for row in self.entries() if row["path"] != str(path)])[:HISTORY_LIMIT])

    def viewer_event(self, event):
        # A project restores anonymous USD layers. Record the project itself
        # after it finishes opening, rather than its temporary viewport source.
        if event["event"] in ("loaded", "saved"):
            self.remember("usd", event["path"])

    def rebuild(self):
        self.clear()
        entries = self.entries()
        names = [Path(row["path"]).name for row in entries]
        for index, entry in enumerate(entries, 1):
            path = Path(entry["path"])
            title = path.name
            if names.count(title) > 1:
                title += " — " + str(path.parent)
            action = self.addAction(f"{index}. {title.replace('&', '&&')} ({KINDS[entry['kind']]})")
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            action.setData(entry)
            action.triggered.connect(lambda checked=False, entry=entry: self.open_entry(entry))
        if not entries:
            self.addAction("No recent scenes").setEnabled(False)
        self.addSeparator()
        clear = self.addAction("Clear Recent Scenes", self.clear_history)
        clear.setEnabled(bool(entries))

    def clear_history(self):
        self.write_entries([])
        self.rebuild()

    def open_entry(self, entry):
        if not Path(entry["path"]).is_file():
            QMessageBox.warning(self.window, "Scene not found", "The file no longer exists:\n" + entry["path"])
            self.write_entries([row for row in self.entries() if row["path"] != entry["path"]])
            return
        # These existing entry points retain their dirty-document checks and
        # only promote the history entry after a successful open.
        callbacks = {"usd": self.window.usd_viewer.open_file, "graph": self.window.load_graph_file}
        callbacks[entry["kind"]](entry["path"])
