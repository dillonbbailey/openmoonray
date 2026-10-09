"""Persistent application preferences, independent of projects and layouts."""
import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Signal, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                              QGroupBox, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit, QMessageBox,
                              QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import recent_scenes
from .shortcuts import DEFAULT_SHORTCUTS, SETTINGS_KEY, SHORTCUTS, load_shortcuts, normalize_shortcuts
from .texture_preferences import cache_folder, default_cache_folder, save_cache_folder

PRECISION_DEFAULTS = {"float": 4, "double": 4}
# Separate compact display preferences from the old always-visible precision.
# Existing installations start with four places without deleting old settings.
PRECISION_KEYS = {kind: "application/" + kind + "CompactDecimalPlaces" for kind in PRECISION_DEFAULTS}
MAX_DECIMALS = 17


class ApplicationPreferences(QObject):
    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        # A small JSON sidecar lets both Python runtimes read live preferences
        # without importing each other's incompatible Qt bindings.
        self.texture_config = Path(store.fileName()).with_name("texture-cache.json")
        os.environ["MOONLAB_TEXTURE_PREFERENCES"] = str(self.texture_config)
        self.texture_cache = cache_folder(self.texture_config)
        self.precisions = {}
        self.shortcuts = load_shortcuts(store)
        for kind, default in PRECISION_DEFAULTS.items():
            try:
                value = int(store.value(PRECISION_KEYS[kind], default))
            except (ValueError, TypeError):
                value = default
            self.precisions[kind] = value if 0 <= value <= MAX_DECIMALS else default

    def save(self, values, shortcuts=None, texture_cache=None):
        if set(values) != set(PRECISION_DEFAULTS) or any(
                type(value) is not int or not 0 <= value <= MAX_DECIMALS for value in values.values()):
            raise ValueError(f"Precision must be between 0 and {MAX_DECIMALS} decimal places.")
        shortcuts = normalize_shortcuts(self.shortcuts if shortcuts is None else shortcuts)
        folder = self.texture_cache
        if texture_cache is not None and texture_cache != folder:
            folder = save_cache_folder(texture_cache, self.texture_config)
        for kind, value in values.items():
            self.store.setValue(PRECISION_KEYS[kind], value)
        self.store.setValue(SETTINGS_KEY, json.dumps(shortcuts))
        self.store.sync()
        if self.store.status() != QSettings.Status.NoError:
            raise OSError("Could not save application settings.")
        if self.precisions != values or self.shortcuts != shortcuts or self.texture_cache != folder:
            self.precisions = dict(values)
            self.shortcuts = shortcuts
            self.texture_cache = folder
            self.changed.emit()


def preferences():
    app = QApplication.instance()
    store = recent_scenes.settings_store()
    current = getattr(app, "numeric_preferences", None)
    if current is None or current.store.fileName() != store.fileName():
        current = ApplicationPreferences(store, app)
        app.numeric_preferences = current
    return current


class ApplicationSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("application_settings")
        self.setWindowTitle("Application Settings")
        self.resize(760, 610)
        self.preferences = preferences()
        layout = QVBoxLayout(self)
        group = QGroupBox("Minimum numeric display precision")
        form = QFormLayout(group)
        self.fields = {}
        for kind, title in (("float", "Float precision"), ("double", "Double precision")):
            field = QSpinBox()
            field.setObjectName(kind + "_precision")
            field.setRange(0, MAX_DECIMALS)
            field.setSuffix(" decimal places")
            field.setValue(self.preferences.precisions[kind])
            field.setToolTip("Minimum displayed decimal places for " + kind + " fields. Nonzero extra digits in loaded or edited values stay visible. "
                             "In spin boxes, place the caret after the last fractional digit and scroll up to reveal more places without changing the value.")
            form.addRow(title, field)
            self.fields[kind] = field
        layout.addWidget(group)
        note = QLabel("Fields show at least the configured number of places, retaining meaningful extra digits from loaded files, "
                      "typed edits, and viewport transforms. Trailing zeros beyond that minimum are removed. In spin boxes, "
                      "scroll at the end of the fractional digits to reveal extra places; focus loss hides unused extra places. "
                      "Large and very small values use e notation. Display formatting never rounds stored values.")
        note.setWordWrap(True)
        layout.addWidget(note)
        textures = QGroupBox("Textures")
        texture_form = QFormLayout(textures)
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(2)
        self.texture_cache = QLineEdit(self.preferences.texture_cache)
        self.texture_cache.setObjectName("texture_cache_folder")
        self.texture_cache.setToolTip("Cached tiled, mipmapped .tx copies. Source files remain unchanged. "
                                     "Changing this folder affects new conversions; existing caches are retained.")
        browse = QPushButton("…")
        browse.setObjectName("texture_cache_browse")
        browse.setToolTip("Choose texture-cache folder")
        browse.clicked.connect(self.browse_texture_cache)
        folder_layout.addWidget(self.texture_cache, 1)
        folder_layout.addWidget(browse)
        texture_form.addRow(".tx cache folder", folder_row)
        layout.addWidget(textures)
        shortcuts = QGroupBox("Keyboard shortcuts")
        shortcuts_layout = QVBoxLayout(shortcuts)
        self.shortcut_search = QLineEdit()
        self.shortcut_search.setPlaceholderText("Filter shortcuts…")
        self.shortcut_search.setClearButtonEnabled(True)
        self.shortcut_search.textChanged.connect(self.filter_shortcuts)
        shortcuts_layout.addWidget(self.shortcut_search)
        self.shortcut_table = QTableWidget(len(SHORTCUTS), 4)
        self.shortcut_table.setObjectName("shortcut_mapping")
        self.shortcut_table.setHorizontalHeaderLabels(["Action", "Context", "Shortcut", "Alternate"])
        self.shortcut_table.verticalHeader().hide()
        self.shortcut_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.shortcut_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.shortcut_fields = {}
        for row, (key, title, context, *_) in enumerate(SHORTCUTS):
            for column, text in enumerate((title, context)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.shortcut_table.setItem(row, column, item)
            fields = []
            for column, value in enumerate(self.preferences.shortcuts[key], 2):
                field = QKeySequenceEdit(QKeySequence(value))
                field.setObjectName(key.replace(".", "_") + f"_shortcut_{column - 1}")
                field.setMaximumSequenceLength(1)
                field.setClearButtonEnabled(True)
                field.setToolTip("Click and press a key combination. Clear to disable this shortcut.")
                self.shortcut_table.setCellWidget(row, column, field)
                fields.append(field)
            self.shortcut_fields[key] = fields
        self.shortcut_table.resizeRowsToContents()
        shortcuts_layout.addWidget(self.shortcut_table)
        shortcuts_layout.addWidget(QLabel("Changes apply after Apply. Restore Defaults resets numbers, texture folder, and shortcuts."))
        layout.addWidget(shortcuts, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Apply |
                                   QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self.save_and_close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.restore_defaults)
        self.buttons = buttons
        layout.addWidget(buttons)

    def filter_shortcuts(self, text):
        for row, (_, title, context, *_) in enumerate(SHORTCUTS):
            self.shortcut_table.setRowHidden(row, text.casefold() not in (title + " " + context).casefold())

    def restore_defaults(self):
        self.texture_cache.setText(default_cache_folder())
        for kind, value in PRECISION_DEFAULTS.items():
            self.fields[kind].setValue(value)
        for key, values in DEFAULT_SHORTCUTS.items():
            for field, value in zip(self.shortcut_fields[key], values):
                field.setKeySequence(QKeySequence(value))

    def browse_texture_cache(self):
        folder = QFileDialog.getExistingDirectory(self, "Texture-cache folder", self.texture_cache.text())
        if folder:
            self.texture_cache.setText(folder)

    def apply(self):
        try:
            shortcuts = {key: [field.keySequence().toString(QKeySequence.SequenceFormat.PortableText) for field in fields]
                         for key, fields in self.shortcut_fields.items()}
            self.preferences.save({kind: field.value() for kind, field in self.fields.items()}, shortcuts,
                                  self.texture_cache.text())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot save application settings", str(exc))
            return False
        return True

    def save_and_close(self):
        if self.apply():
            self.accept()
