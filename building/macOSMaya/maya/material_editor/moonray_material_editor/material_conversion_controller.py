"""Host orchestration for reviewing and applying native USD conversion plans."""
from pathlib import Path
import uuid

from PySide6.QtCore import QObject

from .material_conversion_dialog import MaterialConversionDialog


class MaterialConversionController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.dialog = None
        self.request = None
        self.applying = False
        self.scene = None
        self.revision = None
        self.focus = None
        window.usd_viewer.event_received.connect(self.receive_event)
        window.usd_viewer.convert_materials_requested.connect(self.open)
        window.usd_viewer.history_changed.connect(self.update_action)
        self.update_action()

    def available(self):
        v = self.window.usd_viewer
        return bool(v.path and v.ready and not v.loading and not v.edit_busy and v._time_in_flight is None)

    def update_action(self):
        self.window.convert_materials_action.setEnabled(self.available())

    def open(self, focus=None):
        if not self.available():
            return
        self.focus = focus
        if self.dialog is None:
            path = self.window.usd_viewer.path
            directory = Path(path).parent if not path.startswith("anon:") else Path.home()
            self.dialog = MaterialConversionDialog(self.window.catalog, directory, self.window)
            self.dialog.refresh_requested.connect(self.refresh)
            self.dialog.convert_requested.connect(self.convert)
        self.dialog.show()
        self.dialog.raise_()
        self.refresh()

    def refresh(self):
        if not self.available() or self.applying:
            return
        self.request = uuid.uuid4().hex
        self.scene = self.window.usd_viewer.path
        self.dialog.set_busy(True)
        self.dialog.status.setText("Inspecting the current USD scene…")
        self.window.usd_viewer.send("inspect_material_conversion", request=self.request)

    def convert(self, data):
        if not self.available() or self.applying or self.dialog.stale:
            return
        self.applying = True
        self.request = uuid.uuid4().hex
        self.dialog.set_busy(True)
        self.dialog.status.setText("Copying converted materials to the edit target…" if data.get("storage") == "target"
                                   else "Creating the conversion layer…")
        self.window.usd_viewer.edit_command("convert_materials", request=self.request,
            scene=self.scene, revision=self.revision, **data)

    def receive_event(self, event):
        kind = event["event"]
        if kind in ("loaded", "scene_closed", "worker_stopped"):
            self.request = None
            self.applying = False
            if self.dialog:
                self.dialog.set_busy(False)
                self.dialog.invalidate("The USD scene changed. Refresh to inspect the current scene.")
        elif kind == "material_conversion_inventory" and event.get("request") == self.request:
            if event["scene"] == self.window.usd_viewer.path:
                self.scene, self.revision = event["scene"], event["revision"]
                target = self.window.usd_viewer.layers.entries.get(event.get("edit_target"), {})
                if target.get("name"):
                    event = dict(event, edit_target_name=target["name"])
                self.dialog.load(event, self.focus)
                self.focus = None
            self.request = None
        elif kind == "materials_converted" and event.get("request") == self.request:
            self.request = None
            self.applying = False
            self.dialog.set_busy(False)
            self.dialog.created = event["materials"]
            self.dialog.show_details()
            created = event.get("created_layer", True)
            self.dialog.invalidate(f"Created {len(event['materials'])} materials and {event['bindings']} binding overrides in {event['identifier']}. "
                                   + ("USD Undo removes the conversion layer. " if created else "USD Undo restores the edit target's previous contents. Save the layer to persist edits. ")
                                   + "Refresh to inspect the result.")
            self.window.usd_viewer.layers.select_layer(event["identifier"])
            self.window.statusBar().showMessage("MoonRay materials created in a new sublayer" if created else
                                               "MoonRay materials copied to the current edit target", 10000)
        elif kind == "error" and event.get("request") == self.request and event.get("command") in ("inspect_material_conversion", "convert_materials"):
            self.request = None
            self.applying = False
            self.dialog.show_error(event["message"])
            self.dialog.stale = True
            self.dialog.update_enabled()
        elif kind in ("stage_changed", "time", "frame_range") and self.dialog and not self.applying:
            self.dialog.invalidate()
        elif kind == "layers" and self.dialog and not self.applying and self.dialog.report:
            active = next((row["identifier"] for row in event["layers"] if row.get("active")), None)
            if self.dialog.storage.currentData() == "target" and active != self.dialog.report.get("edit_target"):
                self.dialog.invalidate("The edit target changed. Refresh the table before converting.")
        self.update_action()
