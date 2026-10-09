"""MayaStageBridge: the editor's view of USD scenes in Maya.

In MoonLab the editor's USD scene features (USD SCENE NODES, Apply to USD
material, material sync, light / camera / transform links, conversion) talk to
UsdViewerPanel, which drives a separate usdview process over JSON messages
(`send` / `edit_command` requests, answered by `event_received` events). Here
the same interface is answered for mayaUsd stages in Maya.

Not connected to a stage yet: `ready` stays False, so those features show as
unavailable and every request reports that. Opening a file loads it as a
mayaUsd stage.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction


class _Log(QObject):
    message_logged = Signal(str)


class _Value:
    """Stands in for the viewer's frame spin box."""
    def __init__(self, value=0.0):
        self._value = value

    def value(self):
        return self._value


class _Inspector:
    selection = None


class _Layers:
    entries = {}

    def select_layer(self, identifier):
        pass


class _PrimUsda:
    def __init__(self, bridge):
        self.bridge = bridge

    def show_prim(self, path):
        self.bridge.show_error("Viewing a prim as USDA is not available in Maya yet: " + path)


class MayaStageBridge(QObject):
    # UsdViewerPanel's signals used by the editor and its controllers.
    event_received = Signal(dict)
    bind_graph_requested = Signal(list, str)
    edit_shader_requested = Signal(str)
    edit_light_requested = Signal(str)
    add_camera_requested = Signal(str)
    convert_materials_requested = Signal(str)
    history_changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.log = _Log(self)
        self.path = None
        self.ready = False
        self.loading = False
        self.edit_busy = False
        self.playing = False
        self._time_in_flight = None
        self.selected_prim_paths = []
        self.material_choices = None
        self.prepare_save = None
        self.frame = _Value()
        self.inspector = _Inspector()
        self.layers = _Layers()
        self.prim_usda = _PrimUsda(self)
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        # USD Viewer's "Sync materials" master switch.
        self.sync_materials = QAction("Sync materials", self)
        self.sync_materials.setCheckable(True)
        self.sync_materials.setChecked(True)

    def send(self, command, **arguments):
        self._unavailable(command, arguments)

    def edit_command(self, command, **arguments):
        self._unavailable(command, arguments)

    def _unavailable(self, command, arguments):
        event = dict(event="error", command=command, message="No mayaUsd stage is connected to the Material Editor yet.")
        if "request" in arguments:
            event["request"] = arguments["request"]
        QTimer.singleShot(0, lambda: self.event_received.emit(event))

    def show_error(self, message):
        self.window.statusBar().showMessage(message, 10000)
        self.log.message_logged.emit("ERROR: " + message)

    def open_file(self, path):
        """Load a USD file as a mayaUsd stage (a mayaUsdProxyShape)."""
        from maya import cmds
        path = str(Path(path).resolve())
        cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        name = Path(path).stem.replace(".", "_")
        transform = cmds.createNode("transform", name=name)
        shape = cmds.createNode("mayaUsdProxyShape", name=name + "Shape", parent=transform)
        cmds.setAttr(shape + ".filePath", path, type="string")
        cmds.connectAttr("time1.outTime", shape + ".time")
        cmds.select(transform)
        self.window.statusBar().showMessage("Loaded USD stage " + path, 10000)
        self.window.recent_scenes.remember("usd", path)

    def shutdown(self):
        self.selection_timer.stop()
