"""MayaStageBridge: the editor's view of USD scenes in Maya.

In MoonLab the editor's USD scene features - USD SCENE NODES, Apply to USD
material, material sync, light / camera / transform links, conversion - talk to
UsdViewerPanel, which drives a separate usdview process over JSON messages:
requests go out with `send` / `edit_command`, answers come back as
`event_received` events. This class answers the same requests in-process for
the mayaUsd stage the user is working on, with the same pure-USD helpers the
usdview worker uses (usd_viewer_worker.py there).

- The current stage follows Maya's selection: the stage of selected USD prims
  or of a selected proxy shape; otherwise the only stage in the scene.
- A scene is identified by its stage's root layer identifier (`path`).
- Edits go into the stage's current edit target (Maya's Layer Editor) and into
  Maya's undo queue (mayaUsd's UsdUndoBlock), one undo chunk per edit.
- The frame is the proxy shape's stage time.
"""
from pathlib import Path
import traceback

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction

from .model import EDITOR_ROOT


class _Log(QObject):
    message_logged = Signal(str)


class _Frame:
    """Stands in for the viewer's frame spin box."""
    def __init__(self, bridge):
        self.bridge = bridge

    def value(self):
        return self.bridge.current_frame()


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
        """MoonLab's "View as USDA": the prim's composed USDA in a text window."""
        stage = self.bridge.stage
        prim = stage.GetPrimAtPath(path) if stage else None
        if not prim:
            self.bridge.show_error("No prim at " + path)
            return
        from pxr import Sdf
        layer = Sdf.Layer.CreateAnonymous(".usda")
        Sdf.CopySpec(stage.Flatten(), prim.GetPath(), layer, Sdf.Path("/" + prim.GetName()))
        from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout
        from PySide6.QtGui import QFontDatabase
        dialog = QDialog(self.bridge.window)
        dialog.setWindowTitle(path + " — USDA")
        dialog.resize(760, 640)
        text = QPlainTextEdit(layer.ExportToString())
        text.setReadOnly(True)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        QVBoxLayout(dialog).addWidget(text)
        dialog.show()


def _cmds():
    from maya import cmds
    return cmds


def _edits_class():
    from .usd_editing import StageEdits

    class MayaStageEdits(StageEdits):
        """MoonLab's StageEdits, undone with Maya's Undo instead of its own stack."""
        def __init__(self, stage):
            target = stage.GetEditTarget()
            super().__init__(stage)
            # StageEdits starts in the root layer; keep the Layer Editor's target.
            stage.SetEditTarget(target)

        def change(self, label, action, layer=None, *, record=True):
            from mayaUsd.lib import UsdUndoBlock
            cmds = _cmds()
            cmds.undoInfo(openChunk=True, chunkName="MoonRay: " + label)
            try:
                with UsdUndoBlock():
                    return super().change(label, action, layer, record=False)
            finally:
                cmds.undoInfo(closeChunk=True)

    return MayaStageEdits


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
        self.path = None           # the current stage's root layer identifier
        self.ready = False
        self.loading = False
        self.edit_busy = False
        self.playing = False
        self._time_in_flight = None
        self.selected_prim_paths = []
        self.material_choices = None
        self.prepare_save = None
        self.frame = _Frame(self)
        self.inspector = _Inspector()
        self.layers = _Layers()
        self.prim_usda = _PrimUsda(self)
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        # USD Viewer's "Sync materials" master switch.
        self.sync_materials = QAction("Sync materials", self)
        self.sync_materials.setCheckable(True)
        self.sync_materials.setChecked(True)

        self.proxy = None          # DAG path of the current mayaUsdProxyShape
        self.stage = None
        self.editing = None
        self.stage_revision = 0
        self.stage_notice = None
        self.texture_inventory = None
        self.texture_state = None
        self.material_library_state = None
        from .usd_camera_graph import CameraWatch
        from .transform_graph import transform_info
        self.camera_watch = CameraWatch()
        self.transform_watch = CameraWatch(transform_info)
        self.report_timer = QTimer(self)
        self.report_timer.setSingleShot(True)
        self.report_timer.setInterval(150)
        self.report_timer.timeout.connect(self.report_scene)
        self.maya_jobs = []
        self.ufe_observer = None
        self.handlers = {
            "bind_material": self.bind_material,
            "bind_scene_material": self.bind_scene_material,
            "select_bound_prims": self.select_bound_prims,
            "get_shader_graph": self.get_shader_graph,
            "apply_shader_graph": self.apply_shader_graph,
            "sync_materials": self.sync_materials_command,
            "get_light_graph": self.get_light_graph,
            "apply_light_graph": self.apply_light_graph,
            "get_camera_graph": self.get_camera_graph,
            "watch_camera_graphs": self.watch_camera_graphs,
            "refresh_camera_graphs": self.watch_camera_graphs,
            "get_transform_choices": self.get_transform_choices,
            "get_transform_graph": self.get_transform_graph,
            "watch_transform_graphs": self.watch_transform_graphs,
            "refresh_transform_graphs": self.watch_transform_graphs,
            "inspect_material_conversion": self.inspect_material_conversion,
            "convert_materials": self.convert_materials,
            "play": lambda data: None,  # Maya owns playback
        }
        QTimer.singleShot(0, self.start_tracking)

    # ----- Maya tracking -------------------------------------------------

    def start_tracking(self):
        try:
            cmds = _cmds()
            if not hasattr(cmds, "scriptJob"):
                return  # outside Maya (tests)
        except ImportError:
            return
        cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        self.maya_jobs = [
            cmds.scriptJob(event=["SelectionChanged", self.maya_selection_changed]),
            cmds.scriptJob(event=["timeChanged", self.time_changed]),
            cmds.scriptJob(event=["NewSceneOpened", self.scene_reset]),
            cmds.scriptJob(event=["SceneOpened", self.scene_reset]),
            cmds.scriptJob(event=["Undo", self.dag_changed]),
            cmds.scriptJob(event=["Redo", self.dag_changed]),
        ]
        # Stages created or deleted (no scriptJob event covers deletion).
        from maya import OpenMaya
        self.maya_callbacks = [
            OpenMaya.MDGMessage.addNodeAddedCallback(self.dag_changed, "mayaUsdProxyShape"),
            OpenMaya.MDGMessage.addNodeRemovedCallback(self.dag_changed, "mayaUsdProxyShape"),
        ]
        import ufe

        bridge = self

        class SelectionObserver(ufe.Observer):
            def __call__(self, notification):
                bridge.maya_selection_changed()

        self.ufe_observer = SelectionObserver()
        ufe.GlobalSelection.get().addObserver(self.ufe_observer)
        self.maya_selection_changed()

    def stop_tracking(self):
        try:
            cmds = _cmds()
            for job in self.maya_jobs:
                if cmds.scriptJob(exists=job):
                    cmds.scriptJob(kill=job, force=True)
            from maya import OpenMaya
            for callback in getattr(self, "maya_callbacks", []):
                OpenMaya.MMessage.removeCallback(callback)
            if self.ufe_observer is not None:
                import ufe
                ufe.GlobalSelection.get().removeObserver(self.ufe_observer)
        except Exception:
            pass
        self.maya_jobs = []
        self.ufe_observer = None

    @staticmethod
    def all_proxies():
        cmds = _cmds()
        return cmds.ls(type="mayaUsdProxyShapeBase", long=True) or []

    def selection(self):
        """(proxy shape DAG path, [prim paths]) per selected USD item / proxy."""
        import ufe
        cmds = _cmds()
        proxies = set(self.all_proxies())
        chosen, prims = None, []
        for item in ufe.GlobalSelection.get():
            text = ufe.PathString.string(item.path())
            if "," in text:
                proxy, prim = text.split(",", 1)
                proxy = (cmds.ls(proxy, long=True) or [proxy])[0]
                if chosen is None:
                    chosen = proxy
                if proxy == chosen:
                    prims.append(prim.split(",")[0])  # drop point-instance segments
            else:
                node = (cmds.ls(text, long=True) or [None])[0]
                if node in proxies:
                    chosen = chosen or node
                elif node:
                    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
                    chosen = chosen or next((s for s in shapes if s in proxies), None)
        return chosen, prims

    def maya_selection_changed(self, *_):
        try:
            proxy, prims = self.selection()
            proxies = self.all_proxies()
            if proxy is None:
                proxy = self.proxy if self.proxy in proxies else (proxies[0] if len(proxies) == 1 else None)
            self.set_proxy(proxy)
            paths = prims if proxy == self.proxy else []
            if paths != self.selected_prim_paths:
                self.selected_prim_paths = paths
                self.inspector.selection = dict(path=paths[-1]) if paths else None
                self.emit_later("selection", paths=paths)
        except Exception as exc:
            self.log.message_logged.emit("Selection tracking: " + str(exc))

    def time_changed(self, *_):
        if self.ready:
            self.emit_later("time", value=self.current_frame())
            self.camera_watch.dirty |= self.camera_watch.paths
            self.transform_watch.dirty |= self.transform_watch.paths
            self.report_timer.start()

    def scene_reset(self, *_):
        self.set_proxy(None)
        QTimer.singleShot(0, self.maya_selection_changed)

    def dag_changed(self, *_):
        # A stage created or (by undo) removed: re-evaluate the current one.
        QTimer.singleShot(0, self.maya_selection_changed)

    def current_frame(self):
        if not self.proxy:
            return 0.0
        try:
            import mayaUsd.ufe as mayaUsdUfe
            return float(mayaUsdUfe.getTime(self.proxy).GetValue())
        except Exception:
            return float(_cmds().currentTime(query=True))

    def set_proxy(self, proxy):
        stage = None
        if proxy:
            import mayaUsd.ufe as mayaUsdUfe
            try:
                stage = mayaUsdUfe.getStage(proxy)
            except Exception:
                stage = None
        if proxy == self.proxy and stage is self.stage:
            return
        same_stage = stage is not None and self.stage is not None and stage == self.stage
        self.proxy = proxy
        if same_stage:
            return
        if self.stage is not None:
            self.close_stage()
        if stage is not None:
            self.open_stage(stage)

    def close_stage(self):
        if self.stage_notice:
            self.stage_notice.Revoke()
            self.stage_notice = None
        self.report_timer.stop()
        self.stage = self.editing = self.texture_inventory = None
        self.texture_state = self.material_library_state = None
        self.path, self.ready = None, False
        self.selected_prim_paths = []
        self.emit_later("scene_closed")
        self.history_changed.emit()

    def open_stage(self, stage):
        from pxr import Tf, Usd, UsdGeom
        from .usd_textures import TextureInventory
        self.stage = stage
        self.editing = _edits_class()(stage)
        layer = stage.GetRootLayer()
        self.path = layer.realPath or layer.identifier
        self.ready = True
        self.stage_revision += 1
        self.texture_inventory = TextureInventory(stage)
        self.camera_watch.watch([])
        self.transform_watch.watch([])
        self.stage_notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self.stage_changed, stage)
        cameras = [str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdGeom.Camera)]
        self.emit_later("loaded", path=self.path, start=stage.GetStartTimeCode(), end=stage.GetEndTimeCode(),
                        fps=stage.GetTimeCodesPerSecond(), up_axis=str(UsdGeom.GetStageUpAxis(stage)),
                        meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage), cameras=cameras)
        self.window.statusBar().showMessage("Material Editor · USD stage " + self.proxy.split("|")[-1], 6000)
        self.history_changed.emit()
        self.report_timer.start(0)

    def stage_changed(self, notice, sender):
        from pxr import UsdShade
        self.stage_revision += 1
        if self.texture_inventory:
            self.texture_inventory.changed(notice)
        for path in [*notice.GetChangedInfoOnlyPaths(), *notice.GetResyncedPaths()]:
            prim = sender.GetPrimAtPath(path.GetPrimPath())
            if (path.IsPrimPath() or path.name == "material:binding"
                    or path.name.startswith(("material:binding:", "collection:"))
                    or prim and (prim.IsA(UsdShade.NodeGraph) or prim.IsA(UsdShade.Shader))):
                self.material_library_state = None
                break
        self.camera_watch.changed(notice)
        self.transform_watch.changed(notice)
        self.report_timer.start()

    # ----- reports (what the usdview worker emits after loads and edits) ---

    def report_scene(self):
        if not self.ready:
            return
        from pxr import Usd, UsdGeom
        from .usd_light_graph import light_class
        from .usd_material_sync import records
        from .usd_preview_camera import record as preview_camera_record
        stage, frame = self.stage, self.current_frame()
        self._emit("stage_changed", cameras=[str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdGeom.Camera)], camera="")
        self._emit("edit_state", dirty=False, undo="", redo="")
        self._emit("preview_camera", scene=self.path, link=preview_camera_record(stage))
        self._emit("scene_cameras", scene=self.path,
                  cameras=[dict(path=str(prim.GetPath()), name=prim.GetName())
                           for prim in stage.Traverse(Usd.TraverseInstanceProxies()) if prim.IsA(UsdGeom.Camera)])
        self._emit("scene_materials", scene=self.path, records=records(stage, self.path, frame))
        self._emit("scene_lights", scene=self.path, revision=self.stage_revision,
                  lights=[dict(path=str(prim.GetPath()), name=prim.GetName(), shader=light_class(prim))
                          for prim in stage.Traverse() if light_class(prim)])
        self.report_textures()
        self.report_camera_graphs()

    def report_textures(self):
        update_materials = self.texture_inventory.dirty or self.material_library_state is None
        textures = self.texture_inventory.records()
        if textures != self.texture_state:
            self.texture_state = textures
            self._emit("scene_textures", scene=self.path, textures=textures)
        if update_materials:
            from .usd_material_conversion import scene_material_records
            materials = scene_material_records(self.stage)
            if materials != self.material_library_state:
                self.material_library_state = materials
                self._emit("scene_material_library", scene=self.path, materials=materials)

    def report_camera_graphs(self, force=False, request=None):
        cameras = self.camera_watch.sample(self.stage, self.path, self.current_frame(), force)
        if cameras or request:
            self._emit("camera_graphs_changed", scene=self.path, cameras=cameras, request=request)
        self.report_transform_graphs()

    def report_transform_graphs(self, force=False, request=None):
        transforms = self.transform_watch.sample(self.stage, self.path, self.current_frame(), force)
        if transforms or request:
            self._emit("transform_graphs_changed", scene=self.path, transforms=transforms, request=request)

    # ----- requests ------------------------------------------------------

    def _emit(self, event, **data):
        self.event_received.emit(dict(data, event=event))

    def emit_later(self, event, **data):
        QTimer.singleShot(0, lambda: self._emit(event, **data))

    def send(self, command, **data):
        # Answer after the caller has recorded its request, as with the worker.
        QTimer.singleShot(0, lambda: self.dispatch(command, data))

    def edit_command(self, command, **data):
        self.send(command, **data)

    def dispatch(self, command, data):
        try:
            handler = self.handlers.get(command)
            if handler is None:
                raise ValueError(f"'{command}' is not available in Maya.")
            if not self.ready:
                raise ValueError("Select a USD stage (or a prim in it) in Maya first.")
            handler(data)
        except Exception as exc:
            self.log.message_logged.emit(traceback.format_exc())
            self.window.statusBar().showMessage(str(exc), 15000)
            self._emit("error", command=command, message=str(exc), path=self.path or "",
                      request=data.get("request"))

    def check_scene_frame(self, scene, frame, message):
        if scene != self.path or frame != self.current_frame():
            raise ValueError(message)

    def bind_material(self, data):
        binding = self.editing.bind_material(data["paths"] if "paths" in data else data["path"],
                                             data["graph"], data.get("base_dir", str(EDITOR_ROOT)))
        self._emit("material_bound", **binding)

    def bind_scene_material(self, data):
        binding = self.editing.bind_scene_material(data["material"], data["paths"])
        self._emit("scene_material_applied", **binding)

    def select_bound_prims(self, data):
        from .usd_material_bindings import bound_prim_paths
        paths = bound_prim_paths(self.stage, data["material"])
        self.select_prims(paths)
        self._emit("bound_prims_selected", material=data["material"], paths=paths)

    def select_prims(self, paths):
        cmds = _cmds()
        if paths:
            cmds.select([self.proxy + "," + path for path in paths], replace=True)
        else:
            cmds.select(clear=True)

    def get_shader_graph(self, data):
        from .usd_shader_graph import linked_graph
        result = linked_graph(self.stage, data["path"], self.path, self.current_frame())
        self._emit("shader_graph", request=data.get("request"), **result)

    def apply_shader_graph(self, data):
        self.check_scene_frame(data["link"]["scene"], data["link"]["frame"],
            "Return to the USD stage and frame where this shader network was opened before applying.")
        link = self.editing.apply_shader_graph(data)
        self._emit("shader_graph_applied", link=link, request=data.get("request"))

    def sync_materials_command(self, data):
        self.check_scene_frame(data["scene"], data["frame"],
            "The USD scene or frame changed before materials could synchronize. Edit again at the current frame.")
        records = self.editing.sync_materials(data["updates"], data.get("preview_camera"), derived=data.get("derived", False))
        self._emit("materials_synced", records=records, request=data["request"])

    def get_light_graph(self, data):
        from .usd_light_graph import linked_graph
        graph = linked_graph(self.stage.GetPrimAtPath(data["path"]), self.path, self.current_frame())
        self._emit("light_graph", graph=graph, scene=self.path, request=data.get("request"))

    def apply_light_graph(self, data):
        if (data["reference"]["scene"] != self.path or data["revision"] != self.stage_revision
                or data["frame"] != self.current_frame()):
            raise ValueError("The USD scene changed while preparing the light. Apply again to use the current scene.")
        selected = self.editing.apply_light_graph(data, self.current_frame())
        self._emit("light_graph_applied", path=selected)

    def get_camera_graph(self, data):
        from .usd_camera_graph import camera_info
        info = camera_info(self.stage.GetPrimAtPath(data["path"]), self.path, self.current_frame())
        self._emit("camera_graph", scene=self.path, camera=info, request=data["request"])

    def watch_camera_graphs(self, data):
        if data["scene"] != self.path:
            raise ValueError("The camera's USD scene is no longer the current stage.")
        self.camera_watch.watch(data["paths"])
        self.report_camera_graphs(force=data.get("request") is not None, request=data.get("request"))

    def get_transform_choices(self, data):
        from pxr import UsdGeom
        if data["scene"] != self.path:
            raise ValueError("The transform's USD scene is no longer the current stage.")
        prims = [dict(path=str(p.GetPath()), type=p.GetTypeName()) for p in self.stage.Traverse() if UsdGeom.Xformable(p)]
        self._emit("transform_choices", scene=self.path, prims=prims, request=data["request"])

    def get_transform_graph(self, data):
        from .transform_graph import transform_info
        if data["scene"] != self.path:
            raise ValueError("The transform's USD scene is no longer the current stage.")
        info = transform_info(self.stage.GetPrimAtPath(data["path"]), self.path, self.current_frame())
        self._emit("transform_graph", scene=self.path, transform=info, request=data["request"])

    def watch_transform_graphs(self, data):
        if data["scene"] != self.path:
            raise ValueError("The transform's USD scene is no longer the current stage.")
        self.transform_watch.watch(data["paths"])
        self.report_transform_graphs(force=data.get("request") is not None, request=data.get("request"))

    def inspect_material_conversion(self, data):
        from .usd_material_conversion import inventory
        report = inventory(self.stage, self.current_frame())
        self._emit("material_conversion_inventory", request=data["request"], scene=self.path,
                  revision=self.stage_revision, **report)

    def convert_materials(self, data):
        if data["scene"] != self.path or data["revision"] != self.stage_revision or data["frame"] != self.current_frame():
            raise ValueError("The scene or frame changed. Refresh the conversion table.")
        from .usd_material_conversion import convert_materials
        conversion = convert_materials(self.editing, data)
        self._emit("materials_converted", request=data["request"], **conversion)

    # ----- host helpers --------------------------------------------------

    def select_items(self):
        """UsdViewerPanel flushes a pending hierarchy selection; Maya's is always current."""

    def show_error(self, message):
        self.window.statusBar().showMessage(message, 10000)
        self.log.message_logged.emit("ERROR: " + message)

    def open_file(self, path):
        """Load a USD file as a mayaUsd stage (a mayaUsdProxyShape) and make it current."""
        cmds = _cmds()
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
        self.stop_tracking()
        self.report_timer.stop()
        if self.stage_notice:
            self.stage_notice.Revoke()
            self.stage_notice = None
