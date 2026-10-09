"""Materials and their authored/upstream shaders in the USD scene library."""
from PySide6.QtCore import QObject, QSignalBlocker, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidgetItem


class SceneMaterialLibrary(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.bound_materials = set()
        self.group = QTreeWidgetItem(["USD SCENE MATERIALS"])
        self.group.setFlags(self.group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.group.setForeground(0, QColor("#8298a9"))
        window.scene_library.insertTopLevelItem(0, self.group)
        self.group.setHidden(True)
        window.usd_viewer.event_received.connect(self.receive_event)

    def receive_event(self, event):
        if event["event"] in ("loaded", "scene_closed", "worker_stopped"):
            self.bound_materials.clear()
            self.group.takeChildren()
            self.group.setHidden(True)
            self.window.update_library_path()
        elif event["event"] == "scene_material_library":
            self.bound_materials = {m["path"] for m in event["materials"] if m.get("has_bound_prims")}
            tree = self.window.scene_library
            item = tree.currentItem()
            selected = self.window.library_item_path(item) if item and item.isSelected() else None
            expanded = {self.group.child(i).data(0, Qt.ItemDataRole.UserRole)["usd_material"]:
                        self.group.child(i).isExpanded() for i in range(self.group.childCount())}
            with QSignalBlocker(tree):
                self.group.takeChildren()
                for material in event["materials"]:
                    row = QTreeWidgetItem([material["name"]])
                    row.setData(0, Qt.ItemDataRole.UserRole, dict(usd_material=material["path"], conversion=material["conversion"]))
                    row.setToolTip(0, material["path"] + ("\nDouble-click to review MoonRay conversion." if material["conversion"] else "\nDouble-click to open this material graph."))
                    self.group.addChild(row)
                    if material["path"] == selected:
                        tree.setCurrentItem(row)
                    for shader in material["shaders"]:
                        child = QTreeWidgetItem([shader["name"]])
                        child.setData(0, Qt.ItemDataRole.UserRole, dict(usd_shader=shader["path"],
                            material=material["path"], conversion=not shader["supported"]))
                        child.setToolTip(0, shader["path"] + "\n" + shader["family"])
                        row.addChild(child)
                        if shader["path"] == selected:
                            tree.setCurrentItem(child)
                    row.setExpanded(expanded.get(material["path"], False))
            self.window.filter_library(self.window.library_search.text())
            self.group.setExpanded(True)
        elif event["event"] in ("bound_prims_selected", "scene_material_applied"):
            count = len(event["paths"])
            text = (f"Selected {count} bound prims" if event["event"] == "bound_prims_selected" else
                    f"Applied material to {count} prims")
            self.window.statusBar().showMessage(text + ": " + event["material"], 10000)
        elif event["event"] == "error" and event.get("command") in ("select_bound_prims", "bind_scene_material"):
            self.window.statusBar().showMessage(event["message"], 15000)

    def available(self):
        viewer = self.window.usd_viewer
        return bool(viewer.path and viewer.ready and not viewer.loading and not viewer.edit_busy)

    def select_bound(self, material):
        if self.available():
            self.window.usd_viewer.selection_timer.stop()
            self.window.usd_viewer.send("select_bound_prims", material=material)

    def apply_to_selected(self, material):
        viewer = self.window.usd_viewer
        if self.available():
            if viewer.selection_timer.isActive():
                viewer.select_items()
            viewer.edit_command("bind_scene_material", material=material, paths=list(viewer.selected_prim_paths))

    def material_menu(self, data):
        menu = QMenu(self.window.scene_library)
        material = data.get("usd_material") or data["material"]
        if "usd_material" in data:
            select = menu.addAction("Select bound prims", lambda: self.select_bound(material))
            has_users = material in self.bound_materials
            select.setEnabled(self.available() and has_users)
            select.setToolTip("Select resolved users, including inherited and collection bindings, for all material purposes."
                              if has_users else "This material has no bound prims in the loaded scene.")
            apply = menu.addAction("Apply to selected prims", lambda: self.apply_to_selected(material))
            apply.setEnabled(self.available() and bool(self.window.usd_viewer.selected_prim_paths))
            apply.setToolTip("Bind this existing material in the active USD edit layer. The entire selection is one Undo step.")
            menu.addSeparator()
        menu.addAction("Review MoonRay conversion…", lambda: self.window.material_conversion.open(material))
        if not data.get("conversion"):
            menu.addAction("Edit in Material Editor", lambda: self.window.add_shader(data))
        menu.addSeparator()
        path = data.get("usd_material") or data["usd_shader"]
        menu.addAction("Copy prim path", lambda: QApplication.clipboard().setText(path))
        return menu
