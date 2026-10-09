"""Modeless source-to-destination review table for USD material conversion."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .material_conversion import recipe

ROLE = Qt.ItemDataRole.UserRole


class MaterialConversionDialog(QDialog):
    refresh_requested = Signal()
    convert_requested = Signal(dict)

    def __init__(self, catalog, directory, parent=None):
        super().__init__(parent)
        self.catalog, self.directory = catalog, Path(directory)
        self.report = None
        self.rows = {}
        self.destinations = {}
        self.created = {}
        self.busy = False
        self.stale = False
        self.setObjectName("material_conversion_dialog")
        self.setWindowTitle("Convert USD materials to MoonRay")
        self.resize(1180, 720)
        self.setWindowFlag(Qt.WindowType.Window, True)
        layout = QVBoxLayout(self)
        description = QLabel("Review source → destination mappings. Checked rows copy new materials and binding overrides to the current edit target, or to a new layer if selected below.")
        description.setWordWrap(True)
        layout.addWidget(description)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by material, shader, status or path…")
        self.search.textChanged.connect(self.filter_rows)
        bar.addWidget(self.search, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_requested)
        bar.addWidget(self.refresh_button)
        self.select_mapped = QPushButton("Select mapped")
        self.select_mapped.clicked.connect(self.select_supported)
        bar.addWidget(self.select_mapped)
        self.select_approximate = QPushButton("Select approximations")
        self.select_approximate.setToolTip("Select visible, bound approximations. Review their limitations before converting.")
        self.select_approximate.clicked.connect(self.select_approximations)
        bar.addWidget(self.select_approximate)
        self.clear_button = QPushButton("Clear selection")
        self.clear_button.clicked.connect(lambda: self.set_checked(False))
        bar.addWidget(self.clear_button)
        layout.addLayout(bar)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("material_conversion_table")
        self.table.setHorizontalHeaderLabels(["Convert", "Source material / shader", "Bindings",
                                             "Destination shader", "Status", "Recommendation"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 64)
        self.table.setColumnWidth(1, 250)
        self.table.setColumnWidth(2, 65)
        self.table.setColumnWidth(3, 220)
        self.table.setColumnWidth(4, 120)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.row_resize = QTimer(self)
        self.row_resize.setSingleShot(True)
        self.row_resize.setInterval(0)
        self.row_resize.timeout.connect(self.table.resizeRowsToContents)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self.row_resize.start())
        self.table.currentCellChanged.connect(lambda *_: self.show_details())
        self.table.itemChanged.connect(lambda *_: self.update_enabled())
        splitter.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setObjectName("material_conversion_details")
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a row to inspect full paths, transferred parameters and conversion limits.")
        splitter.addWidget(self.details)
        splitter.setSizes([350, 230])
        layout.addWidget(splitter, 1)
        output = QHBoxLayout()
        output.addWidget(QLabel("Write to"))
        self.storage = QComboBox()
        self.storage.setObjectName("conversion_storage")
        self.storage.addItem("Current edit target", "target")
        self.storage.addItem("New in memory layer (anon)", "memory")
        self.storage.addItem("New on disk layer", "disk")
        self.storage.currentIndexChanged.connect(self.update_storage)
        output.addWidget(self.storage)
        self.layer_name = QLineEdit("MoonrayLooks.usda")
        self.layer_name.setAccessibleName("Conversion sublayer name")
        self.layer_name.textChanged.connect(self.update_enabled)
        output.addWidget(self.layer_name, 1)
        self.browse = QPushButton("Browse…")
        self.browse.clicked.connect(self.choose_path)
        output.addWidget(self.browse)
        self.target_name = QLabel("Current USD edit target")
        self.target_name.setObjectName("conversion_edit_target")
        output.addWidget(self.target_name, 1)
        layout.addLayout(output)
        self.status = QLabel("Inspecting the current USD scene…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        note = QLabel("Approximate rows are unchecked initially. Checking one accepts its listed limitations.")
        note.setWordWrap(True)
        actions.addWidget(note, 1)
        self.convert = QPushButton("Convert selected")
        self.convert.clicked.connect(self.submit)
        actions.addWidget(self.convert)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.update_storage()
        self.set_busy(True)

    def load(self, report, focus=None):
        self.report = report
        identifier = report.get("edit_target", "")
        name = ("anon:" + identifier.rsplit(":", 1)[-1] if identifier.startswith("anon:")
                else Path(identifier).name) if identifier else "Current USD edit target"
        self.target_name.setText(report.get("edit_target_name", name))
        self.target_name.setToolTip(identifier)
        self.created.clear()
        self.stale = False
        self.rows.clear()
        self.table.setRowCount(0)
        self.destinations.clear()
        for source in report["rows"]:
            index = self.table.rowCount()
            self.table.insertRow(index)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(index, 0, check)
            title = QTableWidgetItem(source["name"] + "\n" + source["family"])
            title.setToolTip(source["path"] + "\n" + source.get("shader", ""))
            title.setData(ROLE, source)
            self.table.setItem(index, 1, title)
            self.table.setItem(index, 2, QTableWidgetItem(str(source["bindings"])))
            combo = QComboBox()
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.destinations[index] = combo
            combo.setAccessibleName("Destination for " + source["name"])
            if source["recommended"]:
                combo.addItems(report["destinations"])
                combo.setCurrentText(source["recommended"])
            else:
                combo.addItem("Keep original" if source.get("native") else "Manual rebuild")
                combo.setEnabled(False)
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(3, 2, 3, 2)
            holder_layout.addWidget(combo)
            self.table.setCellWidget(index, 3, holder)
            self.table.setItem(index, 4, QTableWidgetItem())
            self.table.setItem(index, 5, QTableWidgetItem())
            self.rows[source["path"]] = index
            combo.currentIndexChanged.connect(lambda _, index=index: self.destination_changed(index))
            self.destination_changed(index, initial=True)
        self.table.resizeRowsToContents()
        self.row_resize.start()
        self.set_busy(False)
        self.status.setText(f"{len(report['rows'])} materials · frame {report['frame']:g}. Original material networks are retained; edit-target changes remain unsaved until Save.")
        if focus:
            self.search.clear()
        self.filter_rows()
        if focus in self.rows:
            self.table.selectRow(self.rows[focus])
            self.table.scrollToItem(self.table.item(self.rows[focus], 1))

    def destination_changed(self, row, initial=False):
        source = self.table.item(row, 1).data(ROLE)
        destination = self.destinations[row].currentText()
        try:
            plan = recipe(self.catalog, source, destination if source["recommended"] else None)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            plan = dict(status="Manual", reason="Cannot translate this parameter set: " + str(exc),
                        mapped=[], unmapped=[], notes=[], graph=None)
        self.table.item(row, 4).setText(plan["status"])
        self.table.item(row, 4).setData(ROLE, {k: v for k, v in plan.items() if k != "graph"})
        self.table.item(row, 5).setText(plan["reason"])
        self.table.item(row, 5).setToolTip(plan["reason"])
        check = self.table.item(row, 0)
        if plan["graph"]:
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if initial and plan["status"] == "Mapped" and source["bindings"] else Qt.CheckState.Unchecked)
        else:
            check.setFlags(check.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
        self.table.resizeRowToContents(row)
        self.show_details()
        self.update_enabled()

    def show_details(self):
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 4):
            return
        source = self.table.item(row, 1).data(ROLE)
        plan = self.table.item(row, 4).data(ROLE)
        if not plan:
            return
        lines = ["Source: " + source["path"], "Shader: " + source.get("shader", source["family"]),
                 "Destination: " + self.destinations[row].currentText(),
                 "New material namespace: /MoonrayLooks (a unique scope is chosen if it exists)",
                 "", plan["reason"], "", "Parameter mapping:"]
        lines += plan["mapped"] or ["No parameters can be transferred automatically."]
        if source["path"] in self.created:
            lines += ["", "Created material: " + self.created[source["path"]]]
        if plan["unmapped"]:
            lines += ["", "Not translated: " + ", ".join(plan["unmapped"])]
        lines += ["", *plan["notes"]]
        self.details.setPlainText("\n".join(lines))

    def filter_rows(self, *_):
        query = self.search.text().strip().casefold()
        for row in range(self.table.rowCount()):
            source = self.table.item(row, 1).data(ROLE)
            text = " ".join((source["path"], source["family"], self.destinations[row].currentText(),
                             self.table.item(row, 4).text())).casefold()
            self.table.setRowHidden(row, query not in text)
        self.update_enabled()

    def set_checked(self, checked, mapped_only=False):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                source = self.table.item(row, 1).data(ROLE)
                value = checked and (not mapped_only or self.table.item(row, 4).text() == "Mapped" and source["bindings"])
                item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)

    def select_supported(self):
        self.set_checked(True, mapped_only=True)

    def select_approximations(self):
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row) and self.table.item(row, 4).text() == "Approximation" and self.table.item(row, 1).data(ROLE)["bindings"]:
                self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)

    def update_storage(self):
        disk = self.storage.currentData() == "disk"
        current = self.storage.currentData() == "target"
        self.browse.setVisible(disk)
        self.layer_name.setVisible(not current)
        self.target_name.setVisible(current)
        self.layer_name.setPlaceholderText("New USD filename" if disk else "Anonymous layer name")
        if disk and not Path(self.layer_name.text()).is_absolute():
            self.layer_name.setText(str(self.directory / self.layer_name.text()))
        elif not disk:
            self.layer_name.setText(Path(self.layer_name.text()).name)
        self.update_enabled()

    def choose_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "New MoonRay conversion layer", self.layer_name.text(),
            "USD layers (*.usda *.usdc *.usd)", options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.layer_name.setText(path)

    def update_enabled(self, *_):
        selected = [row for row in range(self.table.rowCount())
                    if self.table.item(row, 0) and self.table.item(row, 0).checkState() == Qt.CheckState.Checked]
        self.convert.setText(f"Convert selected ({len(selected)})")
        hidden = sum(self.table.isRowHidden(row) for row in selected)
        self.convert.setToolTip(f"{len(selected)} checked materials, including {hidden} hidden by the filter.")
        self.convert.setEnabled(bool(self.report) and bool(selected) and not self.busy and not self.stale
                                and (self.storage.currentData() == "target" or bool(self.layer_name.text().strip())))

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.table, self.refresh_button, self.select_mapped, self.select_approximate, self.clear_button,
                       self.storage, self.layer_name, self.browse, self.close_button):
            widget.setEnabled(not busy)
        self.update_enabled()

    def invalidate(self, message="The scene changed. Refresh the table before converting."):
        self.stale = True
        self.status.setText(message)
        self.update_enabled()

    def show_error(self, message):
        self.set_busy(False)
        self.status.setText(message)

    def submit(self):
        if not self.convert.isEnabled():
            return
        storage = self.storage.currentData()
        if storage == "target":
            destination = {"target": self.report.get("edit_target")}
        else:
            value = self.layer_name.text().strip()
            disk = storage == "disk"
            path = Path(value).expanduser()
            if not path.suffix:
                path = path.with_suffix(".usda")
            if path.suffix.lower() not in (".usd", ".usda", ".usdc"):
                self.status.setText("Use a .usd, .usda or .usdc layer name.")
                return
            if disk and (not path.parent.is_dir() or path.exists() or path.is_symlink()):
                self.status.setText("Choose a new file in an existing directory. Existing files are never replaced.")
                return
            if not disk and (path.name != str(path) or any(c in value for c in ("\\", "\n", "\0"))):
                self.status.setText("Enter a layer name without a folder path.")
                return
            destination = {"path": str(path.absolute())} if disk else {"name": str(path)}
        choices = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                choices.append(dict(source=self.table.item(row, 1).data(ROLE)["path"],
                                    destination=self.destinations[row].currentText(),
                                    accept_approximation=self.table.item(row, 4).text() == "Approximation"))
        self.convert_requested.emit(dict(choices=choices, fingerprint=self.report["fingerprint"],
            frame=self.report["frame"], storage=storage, **destination))
