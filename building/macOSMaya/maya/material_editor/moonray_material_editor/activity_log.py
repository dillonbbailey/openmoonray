"""Session-wide activity history, independent of per-job and per-document logs."""
from datetime import datetime
from collections import deque
from itertools import islice
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QStatusBar, QVBoxLayout, QWidget

from .buttons import FittingPushButton
from .log_text import clean_log_text


class OutputLog(QPlainTextEdit):
    """Forward new output only; clearing/restoring a local log never replays it."""
    message_logged = Signal(str)

    def appendPlainText(self, text):
        super().appendPlainText(text)
        if text.strip():
            self.message_logged.emit(text)


class ActivityStatusBar(QStatusBar):
    message_logged = Signal(str)

    def showMessage(self, text, timeout=0):
        super().showMessage(text, timeout)
        if text.strip():
            self.message_logged.emit(text)


class ActivityLog(QWidget):
    def __init__(self, parent=None, *, max_lines=20000):
        super().__init__(parent)
        self.entries = deque(maxlen=max_lines)
        self.filters = {}
        self.setObjectName("application_logging")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("Application activity and output"))
        row.addStretch(1)
        self.save_button = FittingPushButton("Save log…")
        self.save_button.setToolTip("Save all captured messages, including sources hidden by the checkboxes.")
        self.save_button.clicked.connect(self.save_log)
        self.clear_button = FittingPushButton("Clear")
        self.clear_button.setToolTip("Clear the full session history, including hidden sources.")
        row.addWidget(self.save_button)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        sources = QHBoxLayout()
        sources.addWidget(QLabel("Show:"))
        for category in ("Application", "Python", "Materials", "USD Viewer", "RenderView", "Simulation", "MCP", "Other"):
            checkbox = QCheckBox(category)
            checkbox.setObjectName("log_filter_" + category.lower().replace(" ", "_"))
            checkbox.setChecked(True)
            checkbox.setToolTip("Show or hide " + category + " messages. Hidden messages remain in the session history.")
            self.filters[category] = checkbox
            sources.addWidget(checkbox)
        sources.addStretch(1)
        layout.addLayout(sources)
        self.output = QPlainTextEdit()
        self.output.setObjectName("application_log_output")
        self.output.setStyleSheet("QPlainTextEdit { color: #999999; }")
        self.output.setReadOnly(True)
        self.output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setMaximumBlockCount(max_lines)
        self.output.setPlaceholderText("Application actions, exports, render output, USD diagnostics, and Python output appear here.")
        self.output.setToolTip(f"Session history · newest {max_lines:,} lines. Save log includes hidden sources; Clear removes all history.")
        self.clear_button.clicked.connect(self.clear)
        for checkbox in self.filters.values():
            checkbox.toggled.connect(self.refresh)
        layout.addWidget(self.output, 1)

    def category(self, source):
        if source == "Material" or source.startswith("Material · ") or source == "Materials":
            return "Materials"
        if source == "Logging":
            return "Application"
        return source if source in self.filters else "Other"

    def clear(self):
        self.entries.clear()
        self.output.clear()

    def refresh(self):
        scrollbar = self.output.verticalScrollBar()
        follow, position = scrollbar.value() >= scrollbar.maximum(), scrollbar.value()
        self.output.setPlainText("\n".join(line for category, line in self.entries if self.filters[category].isChecked()))
        scrollbar.setValue(scrollbar.maximum() if follow else position)

    def record(self, source, text):
        text = clean_log_text(str(text)).strip("\n")
        if not text.strip():
            return
        timestamp = datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")
        prefix = f"[{timestamp}] [{source}] "
        category = self.category(source)
        lines = [prefix + line for line in text.splitlines()]
        evicted = max(0, len(self.entries) + len(lines) - self.entries.maxlen)
        visible_evicted = sum(self.filters[kind].isChecked() for kind, _ in islice(self.entries, evicted))
        self.entries.extend((category, line) for line in lines)
        scrollbar = self.output.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum()
        position = scrollbar.value()
        if visible_evicted:
            cursor = QTextCursor(self.output.document())
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            if visible_evicted >= self.output.blockCount():
                cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            else:
                cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, visible_evicted)
            cursor.removeSelectedText()
        if self.filters[category].isChecked():
            self.output.appendPlainText("\n".join(lines[-self.entries.maxlen:]))
        scrollbar.setValue(scrollbar.maximum() if follow else position)

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save application log", "lunatic.log", "Log files (*.log);;Text files (*.txt)")
        if not path:
            return False
        try:
            Path(path).write_text("\n".join(line for _, line in self.entries) + "\n", encoding="utf-8")
        except OSError as exc:
            self.record("Logging", f"ERROR: Could not save log: {exc}")
            return False
        self.record("Logging", "Saved log: " + str(Path(path).absolute()))
        return True
