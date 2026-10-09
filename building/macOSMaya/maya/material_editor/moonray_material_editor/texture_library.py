"""Read-only list of texture files used by the loaded USD stage."""
from PySide6.QtCore import QObject, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QTreeWidgetItem, QVBoxLayout, QWidget

from .buttons import FittingPushButton
from .material_preview import PreviewLabel


class TextureLibrary(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.path = None
        self.entry = None
        self.page = QWidget()
        self.page.setObjectName("scene_texture_preview_page")
        layout = QVBoxLayout(self.page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview = PreviewLabel()
        self.preview.setAccessibleName("Selected USD scene texture")
        self.preview.setText("Select a scene texture to preview it.")
        layout.addWidget(self.preview, 1)
        navigation = QHBoxLayout()
        navigation.setSpacing(2)
        self.zoom = QLabel("Fit")
        self.zoom.setObjectName("muted")
        self.preview.zoom_changed.connect(self.zoom.setText)
        navigation.addWidget(self.zoom)
        for title, callback, tip in (
            ("−", lambda: self.preview.zoom(1 / 1.2), "Zoom out"),
            ("+", lambda: self.preview.zoom(1.2), "Zoom in"),
            ("Fit", self.preview.fit_image, "Fit texture preview (F)"),
        ):
            control = FittingPushButton(title)
            control.setFixedSize(27, 19)
            control.setStyleSheet("padding: 0;")
            control.setToolTip(tip)
            control.clicked.connect(callback)
            navigation.addWidget(control)
        navigation.addStretch()
        back = FittingPushButton("Back to material")
        back.setToolTip("Deselect the texture and return to the material preview.")
        back.clicked.connect(window.scene_library.clearSelection)
        navigation.addWidget(back)
        layout.addLayout(navigation)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        window.preview_stack.addWidget(self.page)
        window.texture_previews.updated.connect(self.refresh)
        self.group = QTreeWidgetItem(["USD SCENE TEXTURES"])
        self.group.setFlags(self.group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.group.setForeground(0, QColor("#8298a9"))
        self.group.setToolTip(0, "Texture files in the composed stage, including time samples, missing files and UDIM patterns. Uses the current variants and loaded payloads. Right-click a file to copy its full path.")
        window.scene_library.addTopLevelItem(self.group)
        self.group.setHidden(True)
        window.usd_viewer.event_received.connect(self.receive_event)

    def selection_changed(self):
        item = self.window.scene_library.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item and item.isSelected() and not item.isHidden() else None
        path = data.get("usd_texture") if isinstance(data, dict) else None
        if path == self.path:
            return
        self.path = path
        self.entry = None
        if path:
            self.entry = self.window.texture_previews.request(path, size=2048)
            self.preview.original = QPixmap()
            self.preview.fit_image()
            self.refresh(self.entry["key"])
        self.window.preview_heading.setText("TEXTURE PREVIEW" if path else "MATERIAL PREVIEW")
        self.window.auto.setVisible(not path)
        self.window.preview_stack.setCurrentWidget(self.page if path else self.window.material_preview_page)

    def refresh(self, key):
        # A previous texture may finish decoding after selection has changed.
        if not self.entry or key != self.entry["key"]:
            return
        self.preview.original = QPixmap(self.entry["pixmap"])
        self.preview.setText(self.entry["status"] if self.preview.original.isNull() else "")
        self.preview.update_image()
        self.preview.setToolTip(self.entry["tooltip"] + "\nWheel: zoom · Drag: pan · F: fit")
        self.status.setText(self.entry["status"] + " · Source texture preview")
        self.status.setToolTip(self.entry["tooltip"])

    def copy_path(self, path):
        QApplication.clipboard().setText(path)
        self.window.statusBar().showMessage("Texture file path copied", 5000)

    def receive_event(self, event):
        if event["event"] in ("loaded", "scene_closed", "worker_stopped"):
            self.group.takeChildren()
            self.group.setHidden(True)
            self.window.update_library_path()
        elif event["event"] == "scene_textures":
            current = self.window.scene_library.currentItem()
            selected = self.window.library_item_path(current) if current and current.isSelected() else ""
            with QSignalBlocker(self.window.scene_library):
                self.group.takeChildren()
                for texture in event["textures"]:
                    row = QTreeWidgetItem([texture["name"]])
                    row.setData(0, Qt.ItemDataRole.UserRole, dict(usd_texture=texture["path"], usd_usages=texture["usages"]))
                    uses = texture["usages"]
                    details = "\n".join(uses[:12])
                    if len(uses) > 12:
                        details += f"\n… and {len(uses) - 12} more"
                    row.setToolTip(0, texture["path"] + "\nUsed by:\n" + details)
                    self.group.addChild(row)
                    if texture["path"] == selected:
                        self.window.scene_library.setCurrentItem(row)
            self.window.filter_library(self.window.library_search.text())
            self.group.setExpanded(True)
