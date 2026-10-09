"""MoonRay Material Editor for Maya 2027.

A one-off copy of MoonLab's Material Editor window (moonray_editor/app.py,
https://github.com/dillonbbailey/MoonLab) with the same contents - shader
library, material graph tabs, preview, Parameters / Preview setup / Render log -
hosted in a dockable Maya workspace control (see maya_host.py). Removed: the
Lunatic application around it (USD Viewer, RenderView, projects, MCP, Python
console, simulation, layouts, restart). USD scene features talk to mayaUsd
stages through MayaStageBridge instead of the usdview worker.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import time
import uuid

from PySide6.QtCore import QIODevice, QMimeData, QProcess, QSaveFile, QSignalBlocker, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFontDatabase, QPixmap, QUndoCommand
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QTabBar, QTabWidget,
    QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .numeric_controls import NumericSpinBox, NumericLineEdit

from .canvas import COLORS
from .buttons import FittingPushButton
from .activity_log import ActivityLog, ActivityStatusBar, OutputLog
from .color_ramp import color_ramps, display_color, linear_color, ramp_color_inputs, read_stops, remap_color_inputs
from .color_editor import ColorComponents
from .color_picker import NormalizedColorDialog
from .color_grade import channel_groups, is_rgb_grade, set_channel_mode
from .color_grade_editor import ColorGradeEditor
from .collapsible_section import CollapsibleSection
from .ramp_editor import ColorRampEditor
from .transform_graph import projection_matrix_mode
from .checkbox_style import CHECKBOX_STYLE
from .documents import GraphDocument, document_property
from .graph_clipboard import MIME_TYPE as NODE_MIME_TYPE, encode_nodes, paste_nodes
from .log_text import LogStream
from .render_progress import DEFAULT_PROGRESS_STEP, PROGRESS_STEPS
from .render_progress_bar import RenderProgressBar, logged_percent
from .region_controls import RegionControls
from .render_region import validate_region
from .material_preview import PreviewLabel, PreviewPanel
from .model import CACHE_ROOT, EDITOR_ROOT, Graph, GraphError, LIGHT_SAMPLING, LOCAL_ROOT, LOBE_SAMPLES, RAY_DEPTHS, TERMINALS, VECTOR_TYPES, editable, starter_graph
from .maya_stage import MayaStageBridge
from .recent_scenes import RecentScenes
from .texture_previews import TexturePreviews, TexturePreviewLabel, texture_fields
from .tx_controller import TextureConversions, TextureConversionStatus
from .tx_cache import texture_attribute
from .geometry_settings import TESSELLATION_MAX, TESSELLATION_HELP, ADAPTIVE_HELP
from .usd_render_config import SCENE_PREFIX, setting_specs
from .themes import GROUP_LAYOUT, polish_tree, set_label_indent, set_layout_spacing, set_local_style

LIBRARY_CATEGORIES = ("Material", "Map", "NormalMap", "Displacement", "Volume", "Light", "LightFilter",
                      "DisplayFilter", "RenderOutput", "Camera", "Geometry", "LightSet", "LightFilterSet",
                      "TraceSet", "GeometrySet", "ShadowSet", "ShadowReceiverSet", "Metadata", "UserData")

STYLE = """
QWidget { background: #1b242d; color: #d5e0e8; font-size: 12px; }
QMainWindow, QSplitter { background: #121a21; }
QToolBar { background: #202c36; border: 0; padding: 9px; spacing: 2px; }
QToolButton { padding: 8px 12px; border-radius: 5px; }
QToolButton:hover { background: #354652; }
QMenuBar { background: #18212a; padding: 3px; }
QMenu { background: #24323e; border: 1px solid #465662; padding: 5px; }
QMenu::item { padding: 7px 26px; }
QMenu::item:selected { background: #3e5664; }
QMenu::item:disabled { color: #6e8190; }
QLabel#eyebrow { color: #8299aa; font-size: 10px; font-weight: bold; padding: 3px 0; }
QLabel#heading { color: #f0f5f8; font-size: 18px; font-weight: bold; }
QLabel#muted { color: #889eaf; }
QLabel#preview { background: #111a22; border: 1px solid #33434f; border-radius: 0; color: #8299aa; }
QPushButton { background: #2c3b47; border: 1px solid #3a4d5b; border-radius: 5px; padding: 6px 10px; }
QPushButton:hover { background: #3b5261; border-color: #7a9cac; }
QPushButton:disabled { color: #6e8190; background: #26323b; }
QPushButton#primary { background: #7fcbb5; border-color: #7fcbb5; color: #102820; font-weight: bold; }
QPushButton#primary:hover { background: #9cdfcd; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit { background: #131e27; border: 1px solid #354653; border-radius: 4px; padding: 5px; selection-background-color: #436c72; }
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color: #7fcbb5; }
QLineEdit:disabled, QDoubleSpinBox:disabled { color: #647988; }
QTreeWidget { background: #1b242d; border: 0; outline: 0; }
QTreeWidget#usd_prim_tree { alternate-background-color: #1f2831; }
QTreeWidget::item { padding: 6px 2px; }
QTreeWidget::item:selected { background: #304c52; color: #b9f0df; }
QHeaderView::section { background: #24323d; padding: 8px; border: 0; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: #19242d; width: 8px; }
QScrollBar::handle:vertical { background: #415765; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #17212a; color: #8b9ead; padding: 10px 18px; border-radius: 4px; border-bottom: 2px solid #17212a; }
QTabBar::tab:selected { color: #d6eee7; border-bottom-color: #7fcbb5; }
QSplitter::handle { background: #101820; width: 3px; height: 3px; }
QStatusBar { background: #121b23; color: #94aaba; padding: 5px; }
QToolTip { background: #2d414c; color: #e4eff4; border: 1px solid #678493; padding: 5px; }
""" + CHECKBOX_STYLE


def save_exr_copy(source, target):
    """Copy the original floating-point EXR, replacing only a complete file."""
    source, target = Path(source).resolve(), Path(target).expanduser().absolute()
    if target.suffix.lower() != ".exr":
        raise ValueError("Choose an .exr filename.")
    if source == target.resolve():
        return
    fd, temporary = tempfile.mkstemp(prefix=".render-save-", suffix=".exr", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def label(text, role=None):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    return widget


def button(text, callback, primary=False):
    widget = FittingPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


class DocumentCommand(QUndoCommand):
    def __init__(self, window, document, title, before, after, render=True):
        super().__init__(title)
        self.document = document
        self.window, self.before, self.after, self.render = window, before, after, render
        self.executed = False

    def undo(self):
        self.window.restore(self.before, self.render, self.document)
        self.window.activity_log.record("Material · " + self.document.graph.data["name"], "Undo: " + self.text())

    def redo(self):
        self.window.restore(self.after, self.render, self.document)
        self.window.activity_log.record("Material · " + self.document.graph.data["name"],
                                        ("Redo: " if self.executed else "") + self.text())
        self.executed = True


class MaterialNameEdit(QLineEdit):
    def __init__(self, window):
        super().__init__(window.graph.data["name"])
        self.window = window
        self.setObjectName("material_name")
        self.setAccessibleName("Material name")
        self.setMinimumWidth(220)
        self.setMaximumWidth(560)
        self.setStyleSheet("QLineEdit { font-size: 14px; font-weight: bold; padding: 3px 9px; }")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.setText(self.window.graph.data["name"])
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class MaterialEditor(QMainWindow):
    job_started = Signal(str)
    job_finished = Signal(bool)
    graph = document_property("graph")
    path = document_property("path")
    selected = document_property("selected")
    undo = document_property("undo")
    canvas = document_property("canvas")
    revision = document_property("revision")
    preview_revision = document_property("preview_revision")
    add_position = document_property("add_position")

    def __init__(self, catalog, graph_path=None, auto_render=True, parent=None):
        super().__init__(parent)
        # Maya: the stylesheet applies to this window and its children only -
        # never QApplication, which is Maya's.
        self.setObjectName("moonrayMaterialEditor")
        self.setStyleSheet(STYLE)
        self.catalog = catalog
        self.texture_previews = TexturePreviews(self)
        self.texture_conversions = TextureConversions(self)
        graph = Graph.load(catalog, graph_path) if graph_path else starter_graph(catalog)
        self.document = GraphDocument(graph, self, graph_path)
        self.documents = [self.document]
        self.process = None
        self.job_dir = None
        self.pending_render = False
        self.cancelled = False
        self.revision = 0
        self.document_revision = 0
        self.preview_revision = None
        self.job_id = None
        self.add_position = None
        self.resize(1500, 920)
        self.setMinimumSize(1000, 650)
        self.setWindowTitle("MoonRay Material Editor")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(1100)
        self.timer.timeout.connect(self.render)
        self.inspector_timer = QTimer(self)
        self.inspector_timer.setSingleShot(True)
        self.inspector_timer.timeout.connect(self.build_inspector)
        from .application_settings import preferences
        preferences().changed.connect(self.texture_preferences_changed)
        self._build_ui()
        self._menus()
        from .ramp_bake_controller import RampBakeController
        self.ramp_bakes = RampBakeController(self)
        from .light_graph_controller import LightGraphController
        self.light_graphs = LightGraphController(self)
        from .camera_graph_controller import CameraGraphController
        self.camera_graphs = CameraGraphController(self)
        from .texture_library import TextureLibrary
        self.texture_library = TextureLibrary(self)
        from .transform_graph_controller import TransformGraphController
        self.transform_graphs = TransformGraphController(self)
        from .shader_graph_controller import ShaderGraphController
        self.shader_graphs = ShaderGraphController(self)
        from .material_conversion_controller import MaterialConversionController
        self.material_conversion = MaterialConversionController(self)
        from .scene_material_library import SceneMaterialLibrary
        self.scene_materials = SceneMaterialLibrary(self)
        from .material_sync_controller import MaterialSyncController
        self.material_sync = MaterialSyncController(self)
        self.usd_viewer.prepare_save = self.material_sync.before_snapshot
        if graph_path:
            self.recent_scenes.remember("graph", graph_path)
            self.activity_log.record("Application", "Opened graph: " + str(Path(graph_path).absolute()))
        self.connect_document(self.document)
        self.canvas.load(self.graph)
        self.canvas.select_node(self.graph.data["surface"])
        self.update_title()
        self.statusBar().showMessage("Ready · Drag between sockets to connect shaders")
        polish_tree(self)
        QTimer.singleShot(0, self.canvas.fit_graph)
        if auto_render:
            QTimer.singleShot(300, self.render)

    def _build_ui(self):
        self.activity_log = ActivityLog()
        self.last_scene_workspace = 0
        status = ActivityStatusBar(self)
        status.message_logged.connect(lambda text: self.activity_log.record("Application", text))
        self.setStatusBar(status)
        # The floating Material Editor window's toolbar (MoonLab material_window.py).
        toolbar = QToolBar("Material tools")
        toolbar.setObjectName("materialWindowToolbar")
        toolbar.setMovable(False)
        toolbar.setContentsMargins(4, 1, 9, 1)
        toolbar_style = "QToolBar { padding: 1px 9px 1px 4px; }"
        set_local_style(toolbar, toolbar_style, toolbar_style, explorer=toolbar_style)
        self.addToolBar(toolbar)
        for title, callback in (("Open graph", self.open_graph), ("Save graph", self.save_graph),
                                ("Export USD", self.export_usd), ("Export scene", self.export_rdl)):
            toolbar.addAction(title, callback)
        splitter = self.material_splitter = QSplitter()
        # mayaUsd stages instead of the usdview worker (maya_stage.py).
        self.usd_viewer = MayaStageBridge(self)
        self.usd_viewer.log.message_logged.connect(lambda text: self.activity_log.record("Maya USD", text))
        self.usd_viewer.bind_graph_requested.connect(self.bind_graph_to_usd)
        self.usd_viewer.material_choices = self.material_choices
        self.setCentralWidget(splitter)

        library = QWidget()
        library.setMinimumWidth(205)
        layout = QVBoxLayout(library)
        layout.setContentsMargins(4, 4, 4, 4)
        set_layout_spacing(layout, 2)
        layout.addWidget(label("SHADER LIBRARY", "eyebrow"))
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText("Search shaders, graphs and USD nodes…")
        layout.addWidget(self.library_search)
        self.library = QTreeWidget()
        self.library.setObjectName("moonray_shader_library")
        self.library.setHeaderHidden(True)
        self.library.setIndentation(12)
        favorites = ["DwaBaseMaterial", "DwaLayerMaterial", "DwaMetalMaterial", "CheckerboardMap",
                     "NoiseMap_v2", "NoiseWorleyMap_v3", "NormalDisplacement", "VectorDisplacement",
                     "ConstantColorMap", "ConstantScalarMap", "BlendMap", "OpMap", "ImageMap", "ColorCorrectMap", "RampMap"]
        for title, names in [("ESSENTIALS", favorites)] + [
            (category.upper(), [s for s in sorted(self.catalog.shaders) if self.catalog.category(s) == category])
            for category in LIBRARY_CATEGORIES]:
            group = QTreeWidgetItem([title])
            self.library.addTopLevelItem(group)
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group.setForeground(0, QColor("#8298a9"))
            for shader in names:
                if shader not in self.catalog.shaders:
                    continue
                item = QTreeWidgetItem([shader])
                item.setData(0, Qt.ItemDataRole.UserRole, shader)
                item.setForeground(0, QColor(COLORS.get(self.catalog.category(shader), "#90bac9")))
                item.setToolTip(0, f"Double-click to add {shader}")
                group.addChild(item)
            group.setExpanded(title == "ESSENTIALS")
        self.library.itemDoubleClicked.connect(lambda item, _: self.add_shader(item.data(0, Qt.ItemDataRole.UserRole)))
        self.library_search.textChanged.connect(self.filter_library)
        self.library_splitter = QSplitter(Qt.Orientation.Vertical)
        self.library_splitter.setChildrenCollapsible(False)
        moonray_section = QWidget()
        moonray_layout = QVBoxLayout(moonray_section)
        moonray_layout.setContentsMargins(0, 0, 0, 0)
        moonray_layout.addWidget(label("MOONRAY SHADERS", "eyebrow"))
        moonray_layout.addWidget(self.library, 1)
        self.library_splitter.addWidget(moonray_section)
        graphs_section = QWidget()
        graphs_section.setObjectName("material_graphs_section")
        graphs_layout = QVBoxLayout(graphs_section)
        graphs_layout.setContentsMargins(0, 0, 0, 0)
        graphs_layout.setSpacing(2)
        graphs_layout.addWidget(label("MATERIAL GRAPHS", "eyebrow"))
        self.material_graphs = QListWidget()
        self.material_graphs.setObjectName("material_graphs_list")
        self.material_graphs.setAccessibleName("Material graphs")
        self.material_graphs.setToolTip("Select a graph to open its editor. Closing its tab keeps it here. Right-click to create or delete a graph.")
        self.material_graphs.setMinimumHeight(65)
        self.material_graphs.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.material_graphs.currentItemChanged.connect(self.select_material_graph)
        self.material_graphs.itemClicked.connect(self.select_material_graph)
        self.material_graphs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.material_graphs.customContextMenuRequested.connect(self.show_material_graph_menu)
        graphs_layout.addWidget(self.material_graphs, 1)
        self.library_splitter.addWidget(graphs_section)
        scene_section = QWidget()
        scene_layout = QVBoxLayout(scene_section)
        scene_layout.setContentsMargins(0, 0, 0, 0)
        scene_layout.addWidget(label("USD SCENE NODES", "eyebrow"))
        self.scene_library = QTreeWidget()
        self.scene_library.setObjectName("usd_scene_library")
        self.scene_library.setHeaderHidden(True)
        self.scene_library.setIndentation(12)
        self.scene_library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scene_library.customContextMenuRequested.connect(self.scene_library_context_menu)
        self.scene_library.itemDoubleClicked.connect(lambda item, _: self.add_shader(item.data(0, Qt.ItemDataRole.UserRole)))
        scene_layout.addWidget(self.scene_library, 1)
        self.library_splitter.addWidget(scene_section)
        self.library_splitter.setSizes([330, 150, 280])
        layout.addWidget(self.library_splitter, 1)
        self.add_shader_button = button("+  Add selected shader", self.add_selected_shader)
        layout.addWidget(self.add_shader_button)
        self.library_path = label("", "muted")
        self.library_path.setAccessibleName("Selected USD item path")
        self.library_path.setTextFormat(Qt.TextFormat.PlainText)
        self.library_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.library_path.setWordWrap(True)
        self.library_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.library_path.setMinimumHeight(self.library_path.fontMetrics().height())
        layout.addWidget(self.library_path)
        for tree in (self.library, self.scene_library):
            tree.currentItemChanged.connect(lambda *_, tree=tree: self.library_selection_changed(tree))
            tree.itemSelectionChanged.connect(lambda tree=tree: self.library_selection_changed(tree))
        footer = label(f"{len(self.catalog.shaders)} graph nodes\nMoonRay · OpenUSD 25.11 · Maya 2027", "muted")
        footer.setContentsMargins(0, 12, 0, 0)
        layout.addWidget(footer)
        splitter.addWidget(library)

        graph_panel = QWidget()
        graph_layout = QVBoxLayout(graph_panel)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        tab_row = QHBoxLayout()
        tab_row.setSpacing(2)
        tab_row.setContentsMargins(0, 0, 8, 0)
        self.graph_tabs = QTabBar()
        self.graph_tabs.setObjectName("material_graph_tabs")
        self.graph_tabs.setFixedHeight(30)
        tab_style = "QTabBar::tab { padding-top: 3px; padding-bottom: 3px; }"
        set_local_style(self.graph_tabs, tab_style, tab_style, explorer=tab_style)
        self.graph_tabs.setAccessibleName("Material graphs")
        self.graph_tabs.setExpanding(False)
        self.graph_tabs.setTabsClosable(True)
        self.graph_tabs.setUsesScrollButtons(True)
        self.graph_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.graph_tabs.addTab(self.graph.data["name"])
        self.graph_tabs.currentChanged.connect(self.switch_document)
        self.graph_tabs.tabCloseRequested.connect(self.close_graph)
        self.graph_tabs.tabBarDoubleClicked.connect(lambda _: self.graph_name.setFocus())
        tab_row.addWidget(self.graph_tabs, 1)
        self.new_graph_button = button("+", self.new_graph)
        self.new_graph_button.setFixedSize(30, 30)
        self.new_graph_button.setAccessibleName("New material tab")
        self.new_graph_button.setToolTip("New material tab · Ctrl+N")
        tab_row.addWidget(self.new_graph_button)
        graph_layout.addLayout(tab_row)
        head = QHBoxLayout()
        head.setObjectName("material_graph_header")
        head.setSpacing(2)
        head.setContentsMargins(2, 2, 2, 2)
        material_label = label("MATERIAL NAME", "eyebrow")
        self.graph_name = MaterialNameEdit(self)
        material_label.setBuddy(self.graph_name)
        self.graph_name.editingFinished.connect(self.commit_material_name)
        head.addWidget(material_label)
        head.addWidget(self.graph_name, 1)
        head.addStretch()
        head.addWidget(button("Frame  F", lambda: self.canvas.fit_graph()))
        self.save_material_usd_button = button("Save material USD", self.export_usd)
        self.save_material_usd_button.setObjectName("save_material_usd_button")
        self.save_material_usd_button.setToolTip("Save only the material's surface, displacement and volume networks as ASCII or binary USD.")
        head.addWidget(self.save_material_usd_button)
        graph_layout.addLayout(head)
        self.graph_stack = QStackedWidget()
        self.graph_stack.addWidget(self.canvas)
        self.empty_graph = QLabel("Select a graph in Material Graphs, or right-click there to Create New.")
        self.empty_graph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_graph.setWordWrap(True)
        self.graph_stack.addWidget(self.empty_graph)
        graph_layout.addWidget(self.graph_stack, 1)
        hint = label("  Drag sockets to connect   ·   Scroll to zoom   ·   Middle-drag to pan   ·   Tab to add   ·   Delete to remove", "muted")
        hint.setContentsMargins(8, 10, 8, 10)
        hint.setWordWrap(True)
        graph_layout.addWidget(hint)
        splitter.addWidget(graph_panel)

        right = self.preview_splitter = QSplitter(Qt.Orientation.Vertical)
        right.setMinimumWidth(350)
        preview_panel = PreviewPanel()
        preview_panel.setObjectName("material_preview_panel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(5, 5, 5, 5)
        preview_heading = QHBoxLayout()
        self.preview_heading = label("MATERIAL PREVIEW", "eyebrow")
        preview_heading.addWidget(self.preview_heading)
        preview_heading.addStretch()
        self.auto = QCheckBox("Auto-render")
        self.auto.setObjectName("preview_auto_render")
        self.auto.setAccessibleName("Auto-render material preview")
        self.auto.setToolTip("Automatically render the material preview after changes. Uncheck to render manually.")
        self.auto.setFixedHeight(19)
        self.auto.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.auto.setChecked(True)
        self.auto.toggled.connect(self.auto_changed)
        preview_heading.addWidget(self.auto)
        preview_layout.addLayout(preview_heading)
        self.preview_stack = QStackedWidget()
        self.material_preview_page = QWidget()
        self.preview_stack.addWidget(self.material_preview_page)
        preview_layout.addWidget(self.preview_stack, 1)
        preview_layout = QVBoxLayout(self.material_preview_page)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview = PreviewLabel()
        self.preview_progress = RenderProgressBar()
        preview_layout.addLayout(self.preview_progress.under(self.preview), 1)
        preview_navigation = QHBoxLayout()
        preview_navigation.setObjectName("material_preview_navigation")
        preview_navigation.setSpacing(2)
        self.preview_zoom = label("Fit", "muted")
        set_label_indent(self.preview_zoom, 8)
        self.preview_zoom.setFixedWidth(42)
        self.preview.zoom_changed.connect(lambda text: self.preview_zoom.setText(text.removeprefix("Fit · ")))
        self.preview.zoom_changed.connect(self.preview_zoom.setToolTip)
        self.preview.zoom_changed.connect(lambda _: setattr(self.document, "preview_view", self.preview.view_state()))
        preview_navigation.addWidget(self.preview_zoom)
        for title, width, callback, tip in [
            ("−", 22, lambda: self.preview.zoom(1 / 1.2), "Zoom out"),
            ("+", 22, lambda: self.preview.zoom(1.2), "Zoom in"),
            ("Fit", 25, self.preview.fit_image, "Fit image to panel (F)"),
            ("100%", 38, self.preview.actual_size, "One image pixel per display pixel (1)"),
        ]:
            control = button(title, callback)
            control.setFixedSize(width, 19)
            control.setStyleSheet("padding: 0;")
            control.setToolTip(tip)
            control.setAccessibleName(tip)
            preview_navigation.addWidget(control)
        self.preview_region_controls = RegionControls(self.preview.selection, compact=True)
        self.preview_region_controls.reset.setFixedWidth(50)
        self.preview.selection.changed.connect(lambda: setattr(self.document, "preview_region",
            (self.preview.selection.enabled, self.preview.selection.region)))
        preview_navigation.addWidget(self.preview_region_controls, 1)
        preview_layout.addLayout(preview_navigation)
        self.preview_status = label("Studio sphere · linear → sRGB", "muted")
        preview_layout.addWidget(self.preview_status)
        preview_actions = QHBoxLayout()
        preview_actions.setSpacing(2)
        preview_actions.setObjectName("material_preview_actions")
        self.render_button = button("Render preview", self.render_or_cancel, True)
        preview_actions.addWidget(self.render_button, 1)
        self.moonray_gui_action = QAction("Render material scene in moonray_gui", self)
        self.moonray_gui_action.setToolTip("Open a snapshot of the current material scene in the standalone MoonRay GUI, with preview geometry, lights, camera, settings and display filters.")
        self.moonray_gui_action.triggered.connect(self.render_in_moonray_gui)
        self.render_options = QToolButton()
        self.render_options.setObjectName("material_render_options")
        self.render_options.setAccessibleName("Material render options")
        self.render_options.setToolTip("Render options · open the material scene in moonray_gui")
        self.render_options.setArrowType(Qt.ArrowType.DownArrow)
        self.render_options.setFixedWidth(22)
        self.render_options.setStyleSheet("padding: 0;")
        self.render_options.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        render_menu = QMenu(self.render_options)
        render_menu.addAction(self.moonray_gui_action)
        self.render_options.setMenu(render_menu)
        self.render_options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        preview_actions.addWidget(self.render_options)
        self.save_preview_button = button("Save render", self.save_preview)
        self.save_preview_button.setObjectName("save_preview_button")
        self.save_preview_button.setToolTip("Save the material preview as a full-resolution PNG or original floating-point EXR.")
        self.save_preview_button.setEnabled(False)
        preview_actions.addWidget(self.save_preview_button)
        self.save_preview_usd_button = button("Save USD", lambda: self.export_usd(preview_scene=True))
        self.save_preview_usd_button.setObjectName("save_preview_usd_button")
        self.save_preview_usd_button.setToolTip("Save the current material preview scene as ASCII or binary USD, including geometry, materials, lights, camera and render settings.")
        preview_actions.addWidget(self.save_preview_usd_button)
        preview_layout.addLayout(preview_actions)
        right.addWidget(preview_panel)

        self.tabs = QTabWidget()
        inspector = QWidget()
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(2, 2, 2, 2)
        self.parameter_header = QVBoxLayout()
        inspector_layout.addLayout(self.parameter_header)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(2)
        self.parameter_search = QLineEdit()
        self.parameter_search.setPlaceholderText("Filter parameters…")
        self.parameter_search.textChanged.connect(self.filter_parameters)
        self.all_parameters = QCheckBox("All")
        self.all_parameters.setChecked(True)
        self.all_parameters.setToolTip("Show all parameters, including advanced controls")
        self.all_parameters.toggled.connect(self.build_inspector)
        filter_row.addWidget(self.parameter_search)
        filter_row.addWidget(self.all_parameters)
        inspector_layout.addLayout(filter_row)
        self.inspector = QScrollArea()
        self.inspector.setWidgetResizable(True)
        inspector_layout.addWidget(self.inspector)
        self.tabs.addTab(inspector, "Parameters")
        geometry_panel = QWidget()
        geometry_layout = QVBoxLayout(geometry_panel)
        geometry_layout.setContentsMargins(16, 16, 16, 16)
        geometry_layout.addWidget(label("RENDERING", "eyebrow"))
        render_form = QFormLayout()
        set_layout_spacing(render_form, 2)
        self.mode = QComboBox()
        self.mode.addItem("CPU", "vectorized")
        self.mode.addItem("GPU / XPU", "xpu")
        self.mode.setToolTip("MoonRay execution mode")
        render_form.addRow("Renderer", self.mode)
        self.resolution = QComboBox()
        for size in (256, 512, 1024):
            self.resolution.addItem(f"{size} × {size} px", size)
        self.resolution.setCurrentIndex(1)
        render_form.addRow("Resolution", self.resolution)
        self.mode.currentIndexChanged.connect(self.render_settings_changed)
        self.resolution.currentIndexChanged.connect(self.render_settings_changed)
        self.preview_combos = {}
        choices = {
            "render_view": [("Beauty", "beauty"), ("Albedo · material AOV", "albedo"), ("Roughness · material AOV", "roughness"), ("Shading normal", "normal"), ("Depth", "depth"), ("UV coordinates", "uv"), ("Wireframe", "wireframe")]}
        for key, options in choices.items():
            widget = QComboBox()
            widget.setObjectName("preview_" + key)
            for title, data in options:
                widget.addItem(title, data)
            widget.currentIndexChanged.connect(lambda _, k=key, w=widget: self.set_preview(k, w.currentData()))
            render_form.addRow("Render view", widget)
            self.preview_combos[key] = widget
        geometry_layout.addLayout(render_form)
        geometry_layout.addSpacing(10)
        geometry_layout.addWidget(label("PREVIEW GEOMETRY", "eyebrow"))
        geometry = QComboBox()
        geometry.setObjectName("preview_geometry")
        geometry.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        geometry.setMinimumContentsLength(16)
        geometry.setToolTip("Choose the shape used to preview this material. Automatic uses the graph geometry when connected. With automatic rendering enabled, changes update the render.")
        for title, value in [
            ("Automatic · from active outputs", "auto"), ("Smooth sphere", "sphere"),
            ("Hard surface cube", "cube"), ("Flat card", "card"), ("Draped cloth", "cloth"),
            ("Hair strands · round curves", "hair"), ("Closed volume box", "volume"),
            ("OpenVDB volume", "vdb"), ("Light rig", "light_rig"),
        ]:
            geometry.addItem(title, value)
        geometry.currentIndexChanged.connect(lambda _: self.set_preview("geometry", geometry.currentData()))
        geometry_layout.addWidget(geometry)
        self.preview_combos["geometry"] = geometry
        geometry_layout.addSpacing(10)
        self.preview_numbers = {}
        sample_help = {
            "light_sampling_mode": "Choose uniform or adaptive light sampling. Adaptive selects lights according to their contribution at each surface point.",
            "light_sampling_quality": "Adaptive light sampling quality: 0 samples one light per light sample; 1 samples all lights. Values between 0 and 1 adapt the number of lights to each surface point. Used only in Adaptive mode.",
            "light_samples": "Direct-light samples per light at the primary surface hit. The value is squared: 2 requests 4 samples. Higher values reduce direct-light noise.",
            "bsdf_samples": "MoonRay's shared BSDF sampling control, including diffuse and glossy lobes. The value is squared: 2 requests 4 samples per lobe at the primary hit; the actual count also depends on the material and sampling strategy.\n\nMaterial emission lights nearby surfaces through indirect rays. Increase samples to reduce noise; dim the studio lights to see the effect.",
            "bssrdf_samples": "Subsurface-scattering (BSSRDF) sampling at the primary surface hit. The value is squared: 2 requests 4 samples.",
            "max_diffuse_depth": "Diffuse bounces for emission and other indirect lighting. Use at least 1 for an emissive material to light the preview floor. Total ray depth must also be at least 1. Zero disables diffuse bounces; glossy and mirror rays remain separate.",
            "max_depth": "Total indirect ray depth, including rays that hit emissive materials. Must be at least 1 for emission to illuminate nearby surfaces.",
        }
        def preview_control(form, key, title):
            if key == "light_sampling_mode":
                widget = QComboBox()
                widget.addItem("Uniform", 0)
                widget.addItem("Adaptive", 1)
                widget.currentIndexChanged.connect(lambda _: self.set_preview(key, widget.currentData()))
                self.preview_combos[key] = widget
            else:
                if key == "light_sampling_quality":
                    widget = NumericSpinBox()
                    widget.setRange(0, 1)
                    widget.setSingleStep(0.1)
                else:
                    widget = QSpinBox()
                    widget.setRange(0 if key in RAY_DEPTHS else 1, 32 if key == "samples" else 64)
                widget.setKeyboardTracking(False)
                widget.editingFinished.connect(lambda k=key, w=widget: self.set_preview(k, w.value()))
                self.preview_numbers[key] = widget
            if key in sample_help:
                widget.setToolTip(sample_help[key])
            widget.setObjectName("preview_" + key)
            form.addRow(title, widget)
            if key in sample_help:
                form.labelForField(widget).setToolTip(sample_help[key])
        for title, name, controls in [
            ("Sampling", "sampling", [("samples", "Pixel samples · squared"),
                                      ("bsdf_samples", "BSDF samples · squared"),
                                      ("bssrdf_samples", LOBE_SAMPLES["bssrdf_samples"][0])]),
            ("Light sampling", "light_sampling", [(key, value[0]) for key, value in LIGHT_SAMPLING.items()]),
            ("Ray depth", "ray_depth", [(key, value[0]) for key, value in RAY_DEPTHS.items()]),
        ]:
            box = QGroupBox(title)
            box.setObjectName("preview_group_" + name)
            set_local_style(box, "QGroupBox { border: 1px solid #354653; border-radius: 4px; margin-top: 12px; padding-top: 8px; } QGroupBox::title { subcontrol-origin: margin; left: 8px; }", GROUP_LAYOUT)
            form = QFormLayout(box)
            form.setHorizontalSpacing(2)
            for key, label_text in controls:
                preview_control(form, key, label_text)
            geometry_layout.addWidget(box)
        studio_level = NumericSpinBox()
        studio_level.setObjectName("preview_studio_light_level")
        studio_level.setRange(0, 100)
        studio_level.setSingleStep(0.1)
        studio_level.setKeyboardTracking(False)
        studio_level.setToolTip("Brightness multiplier for the built-in preview studio lights. 1 is the usual rig; 0 leaves material emission as the only illumination. Custom graph lights are unchanged.")
        studio_level.editingFinished.connect(lambda: self.set_preview("studio_light_level", studio_level.value()))
        render_form.addRow("Studio light level", studio_level)
        self.preview_numbers["studio_light_level"] = studio_level
        geometry_layout.addSpacing(10)
        geometry_layout.addWidget(label("OPENVDB", "eyebrow"))
        geometry_layout.addWidget(label("OpenVDB file / density grid"))
        vdb_row = QHBoxLayout()
        vdb_row.setSpacing(2)
        self.vdb_file = QLineEdit()
        self.vdb_file.setPlaceholderText("Choose a .vdb file for OpenVDB previews")
        self.vdb_file.editingFinished.connect(lambda: self.set_preview("vdb_file", self.vdb_file.text()))
        vdb_row.addWidget(self.vdb_file)
        vdb_row.addWidget(button("…", self.choose_vdb))
        geometry_layout.addLayout(vdb_row)
        self.vdb_grid = QLineEdit()
        self.vdb_grid.editingFinished.connect(lambda: self.set_preview("vdb_grid", self.vdb_grid.text()))
        geometry_layout.addWidget(self.vdb_grid)
        geometry_layout.addWidget(label("VDB scale / translation · scene units"))
        self.vdb_scale = NumericSpinBox()
        self.vdb_scale.setRange(0.000001, 1e6)
        self.vdb_scale.setKeyboardTracking(False)
        self.vdb_scale.editingFinished.connect(lambda: self.set_preview("vdb_scale", self.vdb_scale.value()))
        geometry_layout.addWidget(self.vdb_scale)
        self.vdb_offset = NumericLineEdit(json_value=True)
        self.vdb_offset.setToolTip("JSON [x, y, z] translation after scaling")
        self.vdb_offset.editingFinished.connect(self.set_vdb_offset)
        geometry_layout.addWidget(self.vdb_offset)
        geometry_layout.addSpacing(10)
        geometry_layout.addWidget(label("DISPLACEMENT & SUBDIVISION", "eyebrow"))
        self.displacement_enabled = QCheckBox("Show displacement in preview")
        self.displacement_enabled.setObjectName("preview_displacement_enabled")
        self.displacement_enabled.setToolTip("Compare with the original surface. Does not disable displacement in exported materials.")
        geometry_layout.addWidget(self.displacement_enabled)
        geometry_layout.addWidget(label("Subdivision"))
        self.subdivision = QComboBox()
        self.subdivision.addItem("Catmull–Clark · smooth surface", "catmullClark")
        self.subdivision.addItem("Bilinear · retain planar faces", "bilinear")
        self.subdivision.addItem("Polygon mesh", "none")
        geometry_layout.addWidget(self.subdivision)
        geometry_layout.addWidget(label("Tessellation limit per edge"))
        self.mesh_resolution = QSpinBox()
        self.mesh_resolution.setObjectName("preview_mesh_resolution")
        self.mesh_resolution.setRange(1, TESSELLATION_MAX)
        self.mesh_resolution.setToolTip(TESSELLATION_HELP)
        self.mesh_resolution.setKeyboardTracking(False)
        geometry_layout.addWidget(self.mesh_resolution)
        geometry_layout.addWidget(label("Adaptive error · pixels"))
        self.adaptive_error = NumericSpinBox()
        self.adaptive_error.setRange(0, 64)
        self.adaptive_error.setSingleStep(0.25)
        self.adaptive_error.setSpecialValueText("Uniform tessellation")
        self.adaptive_error.setToolTip(ADAPTIVE_HELP)
        self.adaptive_error.setKeyboardTracking(False)
        geometry_layout.addWidget(self.adaptive_error)
        geometry_layout.addWidget(label("Shadow terminator correction"))
        self.shadow_terminator = QComboBox()
        spec = next(s for s in setting_specs({}) if s["key"] == SCENE_PREFIX + "shadow_terminator_fix")
        for title, value in spec["options"]:
            self.shadow_terminator.addItem(title, value)
        self.shadow_terminator.setToolTip(spec["help"])
        self.shadow_terminator.currentIndexChanged.connect(lambda: self.set_preview("shadow_terminator_fix", self.shadow_terminator.currentData()))
        geometry_layout.addWidget(self.shadow_terminator)
        note = label("Automatic geometry selects curves for hair and volume geometry for volume outputs. Hair depth defaults to 10. Set thin geometry on materials used on open cards or cloth.\n\nActive display/AOV outputs override the render view. Set displacement bound padding to cover outward displacement. Geometry, sampling, and displacement settings are saved with the graph.", "muted")
        note.setWordWrap(True)
        geometry_layout.addWidget(note)
        geometry_layout.addStretch()
        geometry_scroll = QScrollArea()
        geometry_scroll.setWidgetResizable(True)
        geometry_scroll.setWidget(geometry_panel)
        self.tabs.addTab(geometry_scroll, "Preview setup")
        self.displacement_enabled.toggled.connect(lambda value: self.set_preview("displacement_enabled", value))
        self.subdivision.currentIndexChanged.connect(lambda _: self.set_preview("subdivision_scheme", self.subdivision.currentData()))
        self.mesh_resolution.editingFinished.connect(lambda: self.set_preview("mesh_resolution", self.mesh_resolution.value()))
        self.adaptive_error.editingFinished.connect(lambda: self.set_preview("adaptive_error", self.adaptive_error.value()))
        self.sync_preview_controls()
        self.log = OutputLog()
        self.log.message_logged.connect(lambda text: self.activity_log.record("Material · " + self.graph.data["name"], text))
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2500)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log_page = QWidget()
        log_layout = QVBoxLayout(self.log_page)
        log_layout.setContentsMargins(8, 8, 8, 8)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(2)
        progress_row.addWidget(label("Progress every"))
        self.progress_step = QComboBox()
        self.progress_step.setObjectName("material_progress_step")
        self.progress_step.setAccessibleName("Render progress increment")
        self.progress_step.setToolTip("Log renderer progress in these percentage increments. Applies to the next render.")
        for step in PROGRESS_STEPS:
            self.progress_step.addItem(f"{step}%", step)
        self.progress_step.setCurrentIndex(self.progress_step.findData(DEFAULT_PROGRESS_STEP))
        progress_row.addWidget(self.progress_step)
        progress_row.addStretch(1)
        log_layout.addLayout(progress_row)
        log_layout.addWidget(self.log, 1)
        self.tabs.addTab(self.log_page, "Render log")
        right.addWidget(self.tabs)
        right.setSizes([460, 450])
        splitter.addWidget(right)
        splitter.setSizes([240, 980, 420])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self.parameter_rows = []
        self.parameter_groups = []
        self.parameter_labels = {}

    def _menu_action(self, menu, title, callback):
        # Maya: an action owned by the editor. Inside Maya, PySide deletes
        # actions made by QMenu.addAction(text, callable) once they are also
        # added elsewhere (see the shortcut scoping below).
        action = QAction(title, self)
        action.triggered.connect(lambda checked=False: callback())
        menu.addAction(action)
        return action

    def _menus(self):
        # MoonLab's menus, keeping the Material Editor commands. Project, USD
        # Viewer, RenderView, console, simulation, appearance and layout items
        # belong to the Lunatic application and are left out.
        from .shortcuts import bind_shortcut, SHORTCUTS
        command_for_key = {primary: key for key, _, context, primary, _ in SHORTCUTS if context == "Application"}
        # Maya: an embedded window's menu bar, not the macOS global one.
        self.menuBar().setNativeMenuBar(False)
        file_menu = self.menuBar().addMenu("File")
        self.recent_scenes = RecentScenes(self)
        file_menu.addMenu(self.recent_scenes)
        file_menu.addSeparator()
        for title, shortcut, callback in [
            ("New material", "Ctrl+N", self.new_graph), ("Open graph…", "Ctrl+O", self.open_graph),
            ("Save graph", "Ctrl+S", self.save_current), ("Save graph as…", "Ctrl+Shift+S", lambda: self.save_current(True)),
            ("Close material tab", "Ctrl+W", lambda: self.close_graph(self.graph_tabs.currentIndex())),
            ("Export material USD…", "Ctrl+E", self.export_usd), ("Export scene RDL…", "Ctrl+Shift+E", self.export_rdl)]:
            action = QAction(title, self)
            bind_shortcut(action, command_for_key[shortcut])
            action.triggered.connect(callback)
            file_menu.addAction(action)
            if title == "Save graph":
                self.save_current_action = action
            elif title == "Save graph as…":
                self.save_as_current_action = action
        examples = file_menu.addMenu("Examples")
        for title, filename in [("Porcelain checker", "porcelain.moonraygraph"), ("Displaced stone", "displaced_stone.moonraygraph"), ("Hair strands", "hair.moonraygraph"), ("Scattering volume", "volume.moonraygraph"), ("OpenVDB smoke", "vdb.moonraygraph"), ("Cloth", "cloth.moonraygraph"), ("Filtered light", "light_filter.moonraygraph"), ("Display compositing", "display_filter.moonraygraph"), ("Texture channels", "texture_channels.moonraygraph"), ("Layered two-sided material", "two_sided.moonraygraph"), ("Camera and scene references", "scene_references.moonraygraph")]:
            self._menu_action(examples, title, lambda checked=False, name=filename: self.open_example(name))
        file_menu.addSeparator()
        self.copy_preview_action = self._menu_action(file_menu, "Copy preview scene to a Maya USD stage", self.copy_preview_to_usd)
        self.copy_preview_action.setToolTip("Copy the current material, preview geometry, camera, and lights into a USD file and load it as a mayaUsd stage.")
        file_menu.addAction(self.moonray_gui_action)
        self.export_gui_action = self._menu_action(file_menu, "Export to RDLA and render with moonray_gui…", self.export_to_moonray_gui)
        self.export_gui_action.setToolTip("Save the material preview scene as RDLA and open it in moonray_gui.")
        edit = self.menuBar().addMenu("Edit")
        undo = self.undo_action = QAction("Undo", self)
        undo.triggered.connect(lambda: self.undo.undo())
        bind_shortcut(undo, "edit.undo")
        edit.addAction(undo)
        redo = self.redo_action = QAction("Redo", self)
        redo.triggered.connect(lambda: self.undo.redo())
        bind_shortcut(redo, "edit.redo")
        edit.addAction(redo)
        edit.addSeparator()
        self.application_settings_action = self._menu_action(edit, "Application Settings…", self.show_application_settings)
        self.update_edit_actions()
        tools_menu = self.menuBar().addMenu("Tools")
        # Maya: MoonLab binds from the USD Viewer's prim menu; here it acts on
        # the USD prims selected in Maya (outliner or viewport).
        self.assign_material_action = self._menu_action(tools_menu, "Assign material to selected USD prims",
            lambda: self.bind_graph_to_usd(list(self.usd_viewer.selected_prim_paths)))
        self.assign_material_action.setToolTip("Bind this tab's material to the USD prims selected in Maya, in the stage's edit target. Maya's Undo reverts it.")
        tools_menu.addAction(self.usd_viewer.sync_materials)
        self.usd_viewer.sync_materials.setText("Sync materials to USD")
        self.usd_viewer.sync_materials.setToolTip("Write graph edits to the USD materials they are linked to, as you edit.")
        tools_menu.addSeparator()
        self.convert_materials_action = self._menu_action(tools_menu, "Convert USD materials to MoonRay…",
            lambda: self.material_conversion.open())
        self.convert_materials_action.setEnabled(False)
        self.convert_materials_action.setToolTip("Review source materials and recommended destinations, then create a conversion sublayer.")
        help_menu = self.menuBar().addMenu("Help")
        self._menu_action(help_menu, "Graph controls", lambda: QMessageBox.information(self, "Graph controls",
            "Double-click a library shader to add it.\nDrag from an output socket to an input.\nRight-click an input or wire to disconnect.\nUse the ○ / ● button in Parameters to expose inputs.\n\nF: frame graph · Tab: shader library menu\nMiddle-drag: pan · Wheel: zoom\nDelete: remove selected nodes or wires\nCtrl+C / Ctrl+V: copy / paste selected nodes\nCtrl+Z / Ctrl+Shift+Z: undo / redo\n\nGraph files preserve the editor layout. USD exports contain a native MoonRay material.\nColor values are scene-linear. The preview is displayed as sRGB.\n\nShortcuts apply while the Material Editor has focus; elsewhere Maya's own hotkeys apply."))
        # Maya: menu shortcuts (Ctrl+S, Ctrl+Z, ...) only while the editor has
        # focus. With the default WindowShortcut context they would fire
        # anywhere in Maya's main window once the editor is docked there.
        for menu_action in self.menuBar().actions():
            for action in menu_action.menu().actions():
                if not action.menu() and not action.isSeparator():
                    # An owner independent of the menu, as MoonLab's
                    # material_window.py does: otherwise PySide deletes
                    # actions made by QMenu.addAction once they are shared.
                    action.setParent(self)
                    action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                    self.addAction(action)

    def active_workspace(self):
        # Lunatic's workspaces: 0 is the Material Editor, the only one here.
        return 0

    def show_material_editor(self):
        # Raise the Maya workspace control (or floating window) holding the editor.
        from . import maya_host
        maya_host.raise_editor(self)
        self.canvas.setFocus()

    def texture_preferences_changed(self):
        self.inspector_timer.start(0)

    def show_application_settings(self):
        from .application_settings import ApplicationSettingsDialog
        dialog = ApplicationSettingsDialog(self)
        previous = dict(dialog.preferences.precisions)
        try:
            dialog.exec()
            if previous != dialog.preferences.precisions:
                values = dialog.preferences.precisions
                self.statusBar().showMessage(f"Application settings saved · float: {values['float']} decimals · double: {values['double']} decimals", 6000)
        finally:
            dialog.deleteLater()

    def connect_document(self, doc):
        canvas = doc.canvas
        canvas.selection.connect(lambda node: self.select(node) if doc is self.document else setattr(doc, "selected", node))
        canvas.connect_requested.connect(self.connect_nodes)
        canvas.disconnect_requested.connect(self.disconnect)
        canvas.delete_requested.connect(self.delete_nodes)
        canvas.copy_requested.connect(lambda nodes: self.copy_graph_nodes(nodes, doc))
        canvas.paste_requested.connect(lambda point: self.paste_graph_nodes(point, doc))
        canvas.moved.connect(self.move_nodes)
        canvas.add_requested.connect(lambda point: self.show_shader_library(point, doc))
        canvas.rename_requested.connect(self.rename_node)
        # A name edit may arrive after a tab switch because it rebuilds its scene.
        canvas.name_edited.connect(lambda node, name: self.set_node_name(node, name, doc), Qt.ConnectionType.QueuedConnection)
        canvas.preview_display_edited.connect(lambda node, srgb: self.set_map_preview_display(node, srgb, doc),
                                               Qt.ConnectionType.QueuedConnection)
        canvas.relayout_requested.connect(lambda: self.relayout_nodes(doc), Qt.ConnectionType.QueuedConnection)
        canvas.bake_requested.connect(lambda node: self.ramp_bakes.choose(doc, node))
        canvas.motion_bake_requested.connect(lambda node: self.ramp_bakes.choose(doc, node, motion_blur=True))
        canvas.cancel_bake_requested.connect(lambda node: self.ramp_bakes.cancel(doc, node))
        canvas.usd_reference = lambda node: self.node_usd_reference(doc, node)
        canvas.view_usda_requested.connect(lambda node: self.view_node_usda(doc, node))
        doc.undo.cleanChanged.connect(self.update_title)
        for signal in (doc.undo.canUndoChanged, doc.undo.canRedoChanged,
                       doc.undo.undoTextChanged, doc.undo.redoTextChanged):
            signal.connect(self.update_edit_actions)

    def cache_document_panels(self):
        doc = self.document
        doc.mode, doc.size = self.mode.currentData(), self.resolution.currentData()
        doc.image = QPixmap(self.preview.original)
        doc.preview_view = self.preview.view_state()
        doc.preview_region = (self.preview.selection.enabled, self.preview.selection.region)
        doc.preview_status = self.preview_status.text()
        doc.log = self.log.toPlainText()

    def show_document_preview(self):
        self.preview_progress.setValue(self.document.render_percent)
        view = self.document.preview_view
        region = self.document.preview_region
        self.preview.original = QPixmap(self.document.image)
        self.preview.clear()
        if self.preview.original.isNull():
            self.preview.setText("A material, in its best light.\n\nRender a preview to begin.")
        else:
            self.preview.update_image()
        self.preview.restore_view(view)
        self.preview.selection.enabled, self.preview.selection.region = region
        if region[1]:
            try:
                validate_region(region[1], self.preview.original.width(), self.preview.original.height())
            except ValueError:
                self.preview.selection.region = None
        self.preview.selection.changed.emit()
        self.save_preview_button.setEnabled(not self.preview.original.isNull())
        self.preview_status.setText(self.document.preview_status)
        self.log.setPlainText(self.document.log)

    def save_preview(self):
        # Keep the displayed image even if a render finishes while the dialog is open.
        image = QPixmap(self.preview.original)
        preview_dir = self.document.preview_dir
        if image.isNull():
            return False
        dialog = QFileDialog(self, "Save material render", str(self.default_export_path("_preview")),
                             "OpenEXR (*.exr);;PNG image (*.png)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.selectNameFilter("OpenEXR (*.exr)")
        dialog.setDefaultSuffix("exr")
        dialog.filterSelected.connect(lambda name: dialog.setDefaultSuffix("exr" if name.startswith("OpenEXR") else "png"))
        try:
            if not dialog.exec():
                return False
            path = dialog.selectedFiles()[0]
        finally:
            dialog.deleteLater()
        try:
            if Path(path).suffix.lower() == ".exr":
                if preview_dir is None:
                    raise GraphError("Render a new material preview before saving an EXR.")
                save_exr_copy(Path(preview_dir.name) / "preview.exr", path)
            elif Path(path).suffix.lower() == ".png":
                output = QSaveFile(path)
                if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                    raise GraphError(output.errorString())
                if not image.save(output, "PNG"):
                    output.cancelWriting()
                    raise GraphError("Could not encode the material preview as PNG")
                if not output.commit():
                    raise GraphError(output.errorString())
            else:
                raise GraphError("Choose a .png or .exr filename.")
        except (GraphError, OSError, ValueError) as exc:
            self.error(f"Could not save material preview: {exc}")
            return False
        self.statusBar().showMessage(f"Saved material preview · {path}", 10000)
        return True

    def switch_document(self, index):
        if not 0 <= index < len(self.documents):
            return
        if self.documents[index] is self.document and self.document.editor_open and self.graph_stack.currentWidget() is self.canvas:
            return
        if not self.commit_active_edits():
            with QSignalBlocker(self.graph_tabs):
                self.graph_tabs.setCurrentIndex(self.documents.index(self.document) if self.document in self.documents else -1)
            return
        self.timer.stop()
        self.pending_render = False
        self.inspector_timer.stop()
        self.cache_document_panels()
        self.document = self.documents[index]
        self.document.editor_open = True
        self.graph_tabs.setTabVisible(index, True)
        self.document_revision += 1
        self.graph_stack.setCurrentWidget(self.canvas)
        with QSignalBlocker(self.mode), QSignalBlocker(self.resolution):
            self.mode.setCurrentIndex(self.mode.findData(self.document.mode))
            self.resolution.setCurrentIndex(self.resolution.findData(self.document.size))
        self.sync_preview_controls()
        self.build_inspector()
        self.update_title()
        self.update_edit_actions()
        self.show_document_preview()
        self.refresh_material_editor()
        if self.preview_revision != self.revision and not (self.process and self.job_document is self.document):
            self.schedule_render()

    def add_document(self, graph, path=None):
        if not self.commit_active_edits():
            return None
        empty = self.document if self.document not in self.documents else None
        doc = GraphDocument(graph, self, path)
        self.documents.append(doc)
        self.connect_document(doc)
        doc.canvas.load(graph)
        doc.canvas.select_node(graph.data["surface"])
        self.graph_stack.addWidget(doc.canvas)
        self.graph_tabs.addTab(graph.data["name"])
        self.graph_tabs.setCurrentIndex(len(self.documents) - 1)
        self.switch_document(len(self.documents) - 1)
        if empty:
            empty.canvas.deleteLater()
            empty.undo.deleteLater()
        self.show_material_editor()
        doc.canvas.fit_graph()
        QTimer.singleShot(0, doc.canvas.fit_graph)
        if hasattr(self, "material_sync"):
            self.material_sync.changed(doc)
        if hasattr(self, "camera_graphs"):
            self.camera_graphs.reconcile()
            self.transform_graphs.reconcile()
        return doc

    def close_graph(self, index):
        if not 0 <= index < len(self.documents):
            return False
        doc = self.documents[index]
        if doc is self.document and not self.commit_active_edits():
            return False
        doc.editor_open = False
        with QSignalBlocker(self.graph_tabs):
            self.graph_tabs.setTabVisible(index, False)
        if doc is self.document:
            self.timer.stop()
            self.pending_render = False
            other = next((i for i, candidate in enumerate(self.documents) if candidate.editor_open), None)
            if other is not None:
                self.graph_tabs.setCurrentIndex(other)
                self.switch_document(other)
        self.refresh_material_editor()
        self.update_material_graphs()
        return True

    def refresh_material_editor(self):
        opened = self.document in self.documents and self.document.editor_open
        self.graph_stack.setCurrentWidget(self.canvas if opened else self.empty_graph)
        self.graph_name.setEnabled(opened)
        self.save_material_usd_button.setEnabled(opened)
        self.preview_splitter.setEnabled(opened)
        if not opened:
            with QSignalBlocker(self.material_graphs):
                self.material_graphs.setCurrentRow(-1)
        self.update_edit_actions()

    def show_material_graph_menu(self, position):
        item = self.material_graphs.itemAt(position)
        if item:
            self.material_graphs.setCurrentItem(item)
        menu = self.material_graph_menu()
        try:
            menu.exec(self.material_graphs.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def material_graph_menu(self):
        menu = QMenu(self.material_graphs)
        menu.addAction("Create New", self.new_graph)
        item = self.material_graphs.currentItem()
        if item:
            identifier = item.data(Qt.ItemDataRole.UserRole)
            menu.addAction("Delete", lambda: self.delete_graph(next((i for i, doc in enumerate(self.documents) if doc.id == identifier), -1)))
        return menu

    def delete_graph(self, index):
        if not 0 <= index < len(self.documents):
            return False
        doc = self.documents[index]
        self.graph_tabs.setCurrentIndex(index)
        self.switch_document(index)
        if self.document is not doc or not self.confirm_discard():
            return False
        self.ramp_bakes.close_document(doc)
        if self.process and self.job_document is doc and self.job_action == "render":
            self.cancel_job()
        self.close_graph(index)
        with QSignalBlocker(self.graph_tabs):
            self.documents.remove(doc)
            self.graph_tabs.removeTab(index)
            if self.document is doc:
                self.document = self.documents[0] if self.documents else GraphDocument(starter_graph(self.catalog), self)
                if not self.documents:
                    self.document.editor_open = False
            self.graph_tabs.setCurrentIndex(self.documents.index(self.document) if self.documents else -1)
        self.graph_stack.removeWidget(doc.canvas)
        doc.preview_dir = None
        doc.canvas.deleteLater()
        doc.undo.deleteLater()
        self.camera_graphs.reconcile()
        self.transform_graphs.reconcile()
        self.update_title()
        self.refresh_material_editor()
        return True

    def update_edit_actions(self, *_):
        if not hasattr(self, "undo_action"):
            return
        for action, label, enabled in ((self.undo_action, "Undo", self.undo.canUndo()),
                                       (self.redo_action, "Redo", self.undo.canRedo())):
            action.setText(label)
            action.setEnabled(enabled and self.document.editor_open)

    def save_current(self, as_new=False):
        self.save_graph(as_new)

    def material_choices(self):
        return [dict(id=doc.id, name=doc.graph.data["name"],
                     bindable=bool(doc.graph.data["surface"] or doc.graph.data["volume"]),
                     tooltip=(str(doc.path) if doc.path else "Unsaved material") +
                         ("" if doc.graph.data["surface"] or doc.graph.data["volume"] else
                          " · Connect a surface or volume output before binding.")) for doc in self.documents]

    def bind_graph_to_usd(self, path, document_id=None):
        if not self.commit_active_edits():
            return
        doc = next((doc for doc in self.documents if doc.id == document_id), None) if document_id else self.document
        if doc is None:
            self.usd_viewer.show_error("This material tab has been closed. Choose another material.")
            return
        paths = [path] if isinstance(path, str) else list(path)
        if not paths:
            self.usd_viewer.show_error("Select USD prims in Maya to assign the material to.")
            return
        self.usd_viewer.edit_command("bind_material", paths=paths, graph=doc.graph.data,
                                     base_dir=str(doc.path.parent if doc.path else EDITOR_ROOT))

    def update_title(self, *args):
        for index, doc in enumerate(self.documents):
            self.graph_tabs.setTabText(index, doc.graph.data["name"] + (" *" if not doc.undo.isClean() else ""))
            self.graph_tabs.setTabToolTip(index, str(doc.path) if doc.path else "Unsaved material")
        self.update_material_graphs()
        # MoonLab's floating Material Editor title (material_window.py).
        dirty = " *" if not self.undo.isClean() else ""
        self.setWindowTitle(self.graph.data["name"] + dirty + " — Material Editor")
        from . import maya_host
        maya_host.update_title(self)
        self.graph_name.setText(self.graph.data["name"])
        self.graph_name.setToolTip("Name the complete material · Enter or click away to save · Escape to cancel\n"
                                   "Export name: " + self.graph.material_export_name())

    def update_material_graphs(self):
        listing = self.material_graphs
        query = self.library_search.text().casefold()
        with QSignalBlocker(listing):
            ids = [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())]
            if ids != [doc.id for doc in self.documents]:
                scroll = listing.verticalScrollBar().value()
                listing.clear()
                for doc in self.documents:
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, doc.id)
                    listing.addItem(item)
                listing.verticalScrollBar().setValue(scroll)
            for index, doc in enumerate(self.documents):
                item = listing.item(index)
                title = doc.graph.data["name"]
                path = str(doc.path) if doc.path else "Unsaved material"
                item.setText(title + (" *" if not doc.undo.isClean() else ""))
                item.setToolTip(title + "\n" + path)
                item.setHidden(bool(query) and query not in (title + " " + path).casefold())
                if doc is self.document and doc.editor_open:
                    listing.setCurrentItem(item)

    def select_material_graph(self, item, previous=None):
        if item is None:
            return
        document_id = item.data(Qt.ItemDataRole.UserRole)
        index = next((i for i, doc in enumerate(self.documents) if doc.id == document_id), None)
        if index is not None:
            self.graph_tabs.setTabVisible(index, True)
            self.graph_tabs.setCurrentIndex(index)
            self.switch_document(index)
        # A pending invalid edit can prevent a tab switch. Keep the list on
        # the document that actually remained active in that case.
        self.update_material_graphs()

    def error(self, exc):
        self.statusBar().showMessage(str(exc), 12000)
        self.log.appendPlainText(f"ERROR: {exc}")

    def apply(self, title, operation, render=True, raise_errors=False, document=None):
        doc = document or self.document
        if doc not in self.documents:
            return False
        candidate = doc.graph.clone()
        try:
            operation(candidate)
            candidate.validate()
        except (GraphError, ValueError, KeyError) as exc:
            if raise_errors:
                raise
            self.error(exc)
            return False
        if candidate.data != doc.graph.data:
            doc.undo.push(DocumentCommand(self, doc, title, copy.deepcopy(doc.graph.data), candidate.data, render))
        return True

    def restore(self, data, render=True, document=None, derived=False):
        doc = document or self.document
        doc.graph = Graph(self.catalog, data)
        if hasattr(self, "ramp_bakes"):
            self.ramp_bakes.reconcile(doc)
        if hasattr(self, "camera_graphs"):
            self.camera_graphs.refresh_graph(doc.graph)
            self.camera_graphs.reconcile()
            self.transform_graphs.refresh_graph(doc.graph)
            self.transform_graphs.reconcile()
        self.document_revision += 1
        doc.canvas.load(doc.graph)
        if doc.selected and not any(n["id"] == doc.selected for n in doc.graph.data["nodes"]):
            doc.selected = None
        if doc is self.document:
            self.sync_preview_controls()
            self.inspector_timer.start(0)
        self.update_title()
        if render:
            doc.revision += 1
            doc.preview_status = "Material changed · preview out of date"
            if doc is self.document:
                self.preview_status.setText(doc.preview_status)
                self.schedule_render()
        if hasattr(self, "material_sync"):
            self.material_sync.changed(doc, derived=derived)
        if hasattr(self, "light_graphs"):
            self.light_graphs.changed(doc)

    @staticmethod
    def library_item_path(item):
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return next((data[key] for key in ("usd_light", "usd_camera", "usd_texture", "usd_material", "usd_shader")
                     if data.get(key)), "") if isinstance(data, dict) else ""

    def node_usd_reference(self, document, node_id):
        node = document.graph.node(node_id) if node_id != "__output__" else {}
        for key in ("usd_light", "usd_camera", "usd_transform"):
            if node.get(key):
                return node[key]
        links = [document.usd_shader_link]
        if hasattr(self, "material_sync"):
            links += [record["link"] for record in self.material_sync.records.values()
                      if record["material_id"] == document.graph.data["material_id"]]
        for link in links:
            if link and node_id == "__output__":
                return dict(scene=link["scene"], path=link["path"])
            if link and node_id in link.get("paths", {}):
                return dict(scene=link["scene"], path=link["paths"][node_id])
        return None

    def view_node_usda(self, document, node_id):
        reference = self.node_usd_reference(document, node_id)
        if not reference:
            return
        viewer = self.usd_viewer
        if reference["scene"] != viewer.path or not viewer.ready or viewer.loading:
            self.error("Open the linked USD scene before viewing this node as USDA.")
            return
        viewer.prim_usda.show_prim(reference["path"])

    def scene_library_menu(self, item=None):
        tree = self.scene_library
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if isinstance(data, dict) and ("usd_material" in data or "usd_shader" in data):
            menu = self.scene_materials.material_menu(data)
        else:
            menu = QMenu(tree)
            if isinstance(data, dict) and data.get("usd_texture"):
                menu.addAction("Copy file path", lambda: self.texture_library.copy_path(data["usd_texture"]))
        if isinstance(data, dict):
            prim_path = next((data[key] for key in ("usd_light", "usd_camera", "usd_transform", "usd_material", "usd_shader") if data.get(key)), None)
            if prim_path:
                action = menu.addAction("View as USDA", lambda: self.usd_viewer.prim_usda.show_prim(prim_path))
                action.setEnabled(self.usd_viewer.ready and not self.usd_viewer.loading)
            elif data.get("usd_usages"):
                sources = menu.addMenu("View as USDA")
                sources.setEnabled(self.usd_viewer.ready and not self.usd_viewer.loading)
                for path in sorted({usage.split(".", 1)[0] for usage in data["usd_usages"]}):
                    sources.addAction(path, lambda _checked=False, prim=path: self.usd_viewer.prim_usda.show_prim(prim))
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Expand All", tree.expandAll)
        menu.addAction("Collapse All", tree.collapseAll)
        return menu

    def scene_library_context_menu(self, position):
        tree = self.scene_library
        item = tree.itemAt(position)
        if item and item.flags() & Qt.ItemFlag.ItemIsSelectable:
            tree.setCurrentItem(item)
        menu = self.scene_library_menu(item)
        try:
            menu.exec(tree.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def selected_library_item(self):
        for tree in (self.scene_library, self.library):
            item = tree.currentItem()
            if item and item.isSelected() and not item.isHidden():
                parent = item.parent()
                while parent and not parent.isHidden():
                    parent = parent.parent()
                if parent is None:
                    return item
        return None

    def library_selection_changed(self, tree):
        if tree.selectedItems():
            other = self.scene_library if tree is self.library else self.library
            with QSignalBlocker(other):
                other.clearSelection()
        self.update_library_path()

    def update_library_path(self, *_):
        item = self.selected_library_item()
        path = self.library_item_path(item) if item and item.isSelected() and not item.isHidden() else ""
        self.library_path.setText(path)
        self.library_path.setToolTip(path)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self.add_shader_button.setEnabled(bool(data) and not (isinstance(data, dict) and "usd_texture" in data))
        self.add_shader_button.setText("Review conversion…" if isinstance(data, dict) and data.get("conversion") else
                                      "Open selected USD node" if isinstance(data, dict) else "+  Add selected shader")
        if hasattr(self, "texture_library"):
            self.texture_library.selection_changed()

    def filter_library(self, query):
        def filter_item(item, inherited=False):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            own = bool(data) and query.casefold() in (item.text(0) + " " + self.library_item_path(item) + " " + item.toolTip(0)).casefold()
            child_matches = [filter_item(item.child(i), inherited or own) for i in range(item.childCount())]
            match = inherited or own or any(child_matches)
            item.setHidden(not match)
            if query and any(child_matches):
                item.setExpanded(True)
            return match
        for tree in (self.library, self.scene_library):
            for i in range(tree.topLevelItemCount()):
                group = tree.topLevelItem(i)
                filter_item(group)
                group.setExpanded(bool(query) or tree is self.scene_library or i == 0)
        self.update_material_graphs()
        self.update_library_path()

    def show_shader_library(self, point, document):
        if document is not self.document:
            return
        canvas = document.canvas
        menu = QMenu("Shader library", canvas)
        menu.setObjectName("shader_library_menu")
        # Large categories such as Map should scroll on smaller screens.
        menu.setStyleSheet("QMenu { menu-scrollable: 1; }")
        for category in LIBRARY_CATEGORIES:
            group = menu.addMenu(category)
            for shader in sorted(self.catalog.shaders):
                if self.catalog.category(shader) == category:
                    group.addAction(shader).setData(shader)
            group.setEnabled(bool(group.actions()))
        try:
            action = menu.exec(canvas.viewport().mapToGlobal(canvas.mapFromScene(point)))
            # A queued project/tab change can run while Qt's menu loop is open.
            if action and document is self.document and document in self.documents:
                self.add_shader(action.data(), position=[point.x(), point.y()])
        finally:
            menu.deleteLater()

    def add_selected_shader(self):
        item = self.selected_library_item()
        if item:
            self.add_shader(item.data(0, Qt.ItemDataRole.UserRole))

    def add_shader(self, shader, *, position=None):
        if not shader:
            return
        if isinstance(shader, dict) and "usd_texture" in shader:
            return
        if isinstance(shader, dict) and ("usd_material" in shader or "usd_shader" in shader):
            if shader.get("conversion"):
                self.material_conversion.open(shader.get("usd_material") or shader["material"])
            else:
                self.shader_graphs.open(shader.get("usd_shader") or shader["usd_material"])
            return
        if isinstance(shader, dict) and "usd_light" in shader:
            self.light_graphs.open(shader["usd_light"])
            return
        if isinstance(shader, dict) and "usd_camera" in shader:
            self.camera_graphs.open(shader["usd_camera"])
            return
        center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        if position is None:
            position = self.add_position or [center.x() - 122, center.y() - 80]
        self.add_position = None
        created = []
        self.apply(f"Add {shader}", lambda g: created.append(g.add(shader, position)["id"]))
        if created:
            self.canvas.select_node(created[0])

    def copy_graph_nodes(self, nodes, document=None):
        doc = document or self.document
        if doc not in self.documents:
            return
        encoded = encode_nodes(doc.graph, nodes, doc.canvas.texture_base())
        if encoded is not None:
            mime = QMimeData()
            mime.setData(NODE_MIME_TYPE, encoded)
            QApplication.clipboard().setMimeData(mime)
            self.statusBar().showMessage(f"Copied {len(nodes)} node(s)", 3000)

    def paste_graph_nodes(self, point, document=None):
        doc = document or self.document
        mime = QApplication.clipboard().mimeData()
        if doc not in self.documents or mime is None or not mime.hasFormat(NODE_MIME_TYPE):
            return
        encoded = bytes(mime.data(NODE_MIME_TYPE))
        created = []
        def change(graph):
            created.extend(paste_nodes(graph, encoded, [point.x(), point.y()], doc.canvas.texture_base()))
        if self.apply("Paste nodes", change, document=doc):
            doc.canvas.select_nodes(created)
            self.statusBar().showMessage(f"Pasted {len(created)} node(s)", 3000)

    def connect_nodes(self, source, target, port, output="out"):
        def change(graph):
            if target == "__output__":
                if output != "out":
                    raise GraphError("Scene outputs require the primary object output")
                graph.set_terminal(port, source)
            else:
                graph.connect(source, target, port, output)
        self.apply("Connect shaders", change)

    def disconnect(self, target, port):
        def change(graph):
            if target == "__output__":
                graph.set_terminal(port, None)
            else:
                graph.disconnect(target, port)
        self.apply("Disconnect shaders", change)

    def delete_nodes(self, nodes, edges):
        def change(graph):
            graph.remove(nodes)
            for edge in edges:
                if edge["target"] == "__output__":
                    graph.set_terminal(edge["input"], None)
                else:
                    graph.data["connections"] = [c for c in graph.data["connections"] if c != edge]
        self.apply("Delete selection", change)

    def move_nodes(self, positions):
        def change(graph):
            for n in graph.data["nodes"]:
                n["position"] = positions[n["id"]]
        self.apply("Move nodes", change, render=False)

    def relayout_nodes(self, document=None):
        doc = document or self.document
        if doc not in self.documents or (doc is self.document and not self.commit_active_edits()):
            return
        positions = doc.canvas.layout_positions()

        def change(graph):
            for node in graph.data["nodes"]:
                node["position"] = positions[node["id"]]

        self.apply("Relayout nodes", change, render=False, document=doc)
        doc.canvas.fit_graph()

    def select(self, node_id):
        self.selected = node_id
        self.inspector_timer.start(0)

    def set_parameter(self, node_id, name, value, document=None):
        def change(graph):
            groups = channel_groups(self.catalog, graph.node(node_id)["shader"])
            if name in {group["switch"] for group in groups.values()}:
                set_channel_mode(graph, node_id, name, value)
            else:
                graph.set_value(node_id, name, value)
        self.apply(f"Set {name}", change, document=document)

    def set_texture_conversion(self, node_id, name, enabled, document):
        def change(graph):
            node = graph.node(node_id)
            names = set(node.get("tx_textures", []))
            names.add(name) if enabled else names.discard(name)
            if names:
                node["tx_textures"] = sorted(names)
            else:
                node.pop("tx_textures", None)
        if self.apply("Use .tx for " + name, change, document=document) and enabled:
            self.texture_conversions.request(document.graph.value(document.graph.node(node_id), name),
                                             document.path.parent if document.path else EDITOR_ROOT, retry=True)

    def set_grade_parameters(self, node_id, title, values, document):
        pending_refresh = self.inspector_timer.isActive()
        def change(graph):
            node = graph.node(node_id)
            for name, value in values.items():
                if not graph.incoming(node_id, name) and graph.value(node, name) != value:
                    graph.set_value(node_id, name, value)
        if self.apply("Set " + title, change, document=document):
            if document is self.document and self.selected == node_id and not pending_refresh:
                # Keep this row alive through focus-out and picker events.
                # External edits, Undo and Redo still rebuild the inspector.
                self.inspector_timer.stop()

    def refresh_scene_links(self, ready, failed):
        self.camera_graphs.before_snapshot(lambda: self.transform_graphs.before_snapshot(ready, failed), failed)

    def set_ramp_parameters(self, node_id, ramp, values, document, order=None):
        pending_refresh = self.inspector_timer.isActive()
        def change(graph):
            for name, value in values.items():
                graph.set_value(node_id, name, value)
            if order is not None:
                remap_color_inputs(graph, node_id, order)
        if self.apply("Edit " + ramp.replace("_", " "), change, document=document):
            if document is self.document and self.selected == node_id and not pending_refresh:
                # The ramp already reflects this edit. Keep its controls alive
                # while tabbing between fields or opening the color dialog.
                # Undo/redo and other parameter edits still rebuild normally.
                self.inspector_timer.stop()
                self.parameter_rows = [(name, True if name == ramp else key, row)
                                       for name, key, row in self.parameter_rows]

    def rename_node(self, node_id):
        node = self.graph.node(node_id)
        name, accepted = QInputDialog.getText(self, "Rename node", "Node name (used for export)", text=node["label"])
        if accepted:
            self.apply("Rename node", lambda g: g.rename(node_id, name), render=False)

    def set_node_name(self, node_id, name, document=None):
        doc = document or self.document
        # An intervening document replacement may have removed the edited node.
        if any(node["id"] == node_id for node in doc.graph.data["nodes"]):
            self.apply("Rename node", lambda g: g.rename(node_id, name), render=False, document=doc)

    def commit_material_name(self):
        if not self.graph_name.isModified():
            return True
        name = self.graph_name.text()
        accepted = self.apply("Rename material", lambda g: g.rename_material(name), render=False)
        self.graph_name.setText(self.graph.data["name"])
        return accepted

    def set_map_preview_display(self, node_id, srgb, document=None):
        doc = document or self.document
        if any(node["id"] == node_id for node in doc.graph.data["nodes"]):
            self.apply("Change map preview display", lambda g: g.node(node_id).update(preview_srgb=srgb),
                       render=False, document=doc)

    def commit_active_edits(self):
        if self.document not in self.documents or not self.document.editor_open:
            return True
        preview_displays = [(node_id, item.srgb_checkbox.isChecked())
                            for node_id, item in self.canvas.nodes.items() if item.srgb_checkbox is not None]
        # Finish focus-out callbacks before replacing the shared inspector.
        focus = QApplication.focusWidget()
        if focus and self.isAncestorOf(focus):
            focus.clearFocus()
        if not self.commit_material_name():
            return False
        # Inline control commits are queued to avoid deleting a widget in its own
        # event. Flush their values before deciding whether this tab is dirty.
        names = [(node_id, item.name_edit.text().strip() or item.node["label"])
                 for node_id, item in self.canvas.nodes.items() if not item.output_node]
        node_ids = {node["id"] for node in self.graph.data["nodes"]}
        for node_id, name in names:
            if node_id in node_ids and self.graph.node(node_id)["label"] != name:
                self.set_node_name(node_id, name)
        for node_id, srgb in preview_displays:
            if node_id not in node_ids:
                continue
            node = self.graph.node(node_id)
            default = self.catalog.category(node["shader"]) != "NormalMap"
            if node.get("preview_srgb", default) != srgb:
                self.set_map_preview_display(node_id, srgb)
        return True

    def default_export_path(self, suffix):
        directory = self.path.parent if self.path else LOCAL_ROOT
        return directory / (self.graph.material_export_name() + suffix)

    def set_preview(self, name, value):
        self.apply("Change preview settings", lambda g: g.data["preview"].update({name: value}))

    def sync_preview_controls(self):
        controls = (self.displacement_enabled, self.subdivision, self.mesh_resolution, self.adaptive_error, self.shadow_terminator, self.vdb_file, self.vdb_grid, self.vdb_scale, self.vdb_offset, *self.preview_combos.values(), *self.preview_numbers.values())
        blockers = [QSignalBlocker(control) for control in controls]
        settings = self.graph.data["preview"]
        self.displacement_enabled.setChecked(settings["displacement_enabled"])
        self.subdivision.setCurrentIndex(self.subdivision.findData(settings["subdivision_scheme"]))
        self.mesh_resolution.setValue(settings["mesh_resolution"])
        self.adaptive_error.setValue(settings["adaptive_error"])
        self.shadow_terminator.setCurrentIndex(self.shadow_terminator.findData(settings["shadow_terminator_fix"]))
        for key, widget in self.preview_combos.items():
            widget.setCurrentIndex(widget.findData(settings[key]))
        for key, widget in self.preview_numbers.items():
            widget.setValue(settings[key])
        quality = self.preview_numbers["light_sampling_quality"]
        quality.setEnabled(settings["light_sampling_mode"] == 1)
        quality.parentWidget().layout().labelForField(quality).setEnabled(quality.isEnabled())
        self.vdb_file.setText(settings["vdb_file"])
        self.vdb_grid.setText(settings["vdb_grid"])
        self.vdb_scale.setValue(settings["vdb_scale"])
        self.vdb_offset.setText(json.dumps(settings["vdb_offset"]))
        del blockers

    def set_vdb_offset(self):
        try:
            self.set_preview("vdb_offset", json.loads(self.vdb_offset.text()))
        except ValueError as exc:
            self.error(exc)

    def choose_vdb(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose OpenVDB volume", str(LOCAL_ROOT), "OpenVDB (*.vdb)")
        if path:
            self.set_preview("vdb_file", path)

    def toggle_port(self, node_id, name, document=None):
        def change(graph):
            node = graph.node(node_id)
            if name in node["ports"]:
                if graph.incoming(node_id, name):
                    raise GraphError("Disconnect this input before hiding its socket")
                node["ports"].remove(name)
            else:
                node["ports"].append(name)
        self.apply("Toggle input socket", change, render=False, document=document)

    def build_inspector(self, *args):
        doc = self.document
        scroll = self.inspector.verticalScrollBar().value()
        header = QWidget()
        header.setObjectName("parameter_node_header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        set_layout_spacing(header_layout, 2)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(2, 2, 2, 2)
        set_layout_spacing(layout, 2)
        self.parameter_rows = []
        self.parameter_groups = []
        self.parameter_labels = {}
        if not self.selected:
            header_layout.addWidget(label("No node selected", "muted"))
            layout.addWidget(label("Select a shader to edit its parameters.", "muted"))
        else:
            node = self.graph.node(self.selected)
            node_id = node["id"]
            header_layout.addWidget(label(node["shader"], "eyebrow"))
            name_edit = QLineEdit(node["label"])
            name_edit.setObjectName("node_name")
            name_edit.setAccessibleName("Selected node name")
            name_edit.setToolTip("Names the graph node and its exported USD/RDL object. Spaces and duplicates are handled automatically.")
            def commit_name():
                self.apply("Rename node", lambda g: g.rename(node_id, name_edit.text()), False, document=doc)
                name_edit.setText(doc.graph.node(node_id)["label"])
            name_edit.editingFinished.connect(commit_name)
            header_layout.addWidget(name_edit)
            export_name = label("Export name: " + self.graph.export_names()[node_id], "muted")
            export_name.setWordWrap(True)
            export_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(export_name)
            material_links = self.material_sync.links(doc) if hasattr(self, "material_sync") else []
            if doc.usd_shader_link or material_links:
                link = doc.usd_shader_link or material_links[0]["link"]
                target = label("USD shader network: " + link["path"] + (f" (+{len(material_links)-1} bindings)" if len(material_links) > 1 else ""), "muted")
                target.setWordWrap(True)
                target.setToolTip(link["scene"])
                layout.addWidget(target)
                apply_shader = button("Apply to USD material", lambda checked=False, d=doc:
                    self.shader_graphs.apply(d) if d.usd_shader_link else self.material_sync.apply_now(d))
                apply_shader.setObjectName("apply_usd_material")
                apply_shader.setEnabled(self.shader_graphs.available(doc) if doc.usd_shader_link else
                                       self.usd_viewer.ready and not self.usd_viewer.edit_busy and not self.material_sync.running)
                apply_shader.setToolTip(f"Update the linked USD shaders and connections. Animated values are keyed at frame {link['frame']:g}; switch the viewer back to that frame to apply. Shared shaders affect all their users. Undo is available in USD Viewer. Reopen the network after external shader changes or USD Undo."
                                       if doc.usd_shader_link else "Update all associated USD materials at the current frame, including when Auto update or Sync materials is off.")
                auto = QCheckBox("Auto update")
                auto.setObjectName("auto_update_usd_material")
                auto.setChecked(doc.graph.data.get("usd_auto_update", True))
                auto.setEnabled(self.usd_viewer.ready and link["scene"] == self.usd_viewer.path)
                auto.setToolTip("Automatically apply this shader network's edits to its USD materials. USD Viewer's Sync materials checkbox is the master switch. Uncheck to use Apply manually.")
                auto.toggled.connect(lambda enabled, d=doc: self.material_sync.set_auto_update(d, enabled))
                row = QHBoxLayout()
                row.addWidget(apply_shader, 1)
                row.addWidget(auto)
                layout.addLayout(row)
            if node.get("usd_camera"):
                reference = node["usd_camera"]
                target = label(f"USD camera: {reference['path']} · frame {reference['frame']:g}", "muted")
                target.setWordWrap(True)
                target.setToolTip(reference["scene"])
                layout.addWidget(target)
                follow = QCheckBox("Auto update")
                follow.setObjectName("follow_usd_camera")
                follow.setChecked(reference.get("follow", True))
                follow.setToolTip("Follow the source camera's world transform and lens at the current USD frame. Uncheck to freeze the projection and edit its camera values here. The last captured values are kept when the source is unavailable.")
                follow.toggled.connect(lambda enabled, d=doc, n=node_id: self.camera_graphs.set_follow(d, n, enabled))
                follow.setEnabled(self.usd_viewer.ready and reference["scene"] == self.camera_graphs.scene)
                if follow.isChecked() and (reference["scene"] != self.camera_graphs.scene
                        or reference["path"] not in self.camera_graphs.cameras
                        or reference["path"] in self.camera_graphs.failures):
                    layout.addWidget(label("Source unavailable · using last captured camera", "muted"))
                refresh = button("Refresh from USD camera", lambda checked=False: self.camera_graphs.open(reference["path"], doc, node_id))
                refresh.setObjectName("refresh_usd_camera")
                refresh.setEnabled(self.camera_graphs.available(node))
                refresh.setToolTip("Replace this camera's values with the current USD frame. Connect out to a ProjectCameraMap projector input. This does not change the preview's viewing camera.")
                row = QHBoxLayout()
                row.addWidget(refresh, 1)
                row.addWidget(follow)
                layout.addLayout(row)
            if node.get("usd_light"):
                target = label("USD light: " + node["usd_light"]["path"], "muted")
                target.setWordWrap(True)
                target.setToolTip(node["usd_light"]["scene"])
                layout.addWidget(target)
                if node.get("usd_geometry"):
                    geometry_label = label("Emitting mesh: " + node["usd_geometry"], "muted")
                    geometry_label.setWordWrap(True)
                    layout.addWidget(geometry_label)
                apply_light = button("Apply to USD light", lambda checked=False: self.light_graphs.apply(doc, node_id))
                apply_light.setObjectName("apply_usd_light")
                apply_light.setEnabled(self.light_graphs.available(node))
                apply_light.setToolTip("Update the referenced light. Connected color maps become 512×512 linear textures over 0–1 UVs. Scene-dependent maps use a UV card. Animated inputs are keyed at the viewer's current frame. Undo is available in USD Viewer.")
                auto = QCheckBox("Auto update")
                auto.setObjectName("auto_update_usd_light")
                auto.setChecked(node.get("usd_auto_update", False))
                auto.setEnabled(self.usd_viewer.ready and node["usd_light"]["scene"] == self.light_graphs.scene)
                auto.setToolTip("Apply edits to this USD light automatically after a short pause, including changes to connected color maps. Uncheck to use Apply manually.")
                auto.toggled.connect(lambda enabled, d=doc, n=node_id: self.light_graphs.set_auto_update(d, n, enabled))
                row = QHBoxLayout()
                row.addWidget(apply_light, 1)
                row.addWidget(auto)
                layout.addLayout(row)
            for terminal, category in TERMINALS.items():
                if self.catalog.category(node["shader"]) == category:
                    active = node_id == self.graph.data[terminal]
                    output_button = button(f"✓  Active {terminal}" if active else f"Use as {terminal} output",
                                           lambda checked=False, port=terminal: self.connect_nodes(node_id, "__output__", port))
                    output_button.setMaximumHeight(25)
                    compact_style = "padding-top: 1px; padding-bottom: 1px;"
                    set_local_style(output_button, compact_style, compact_style, explorer="")
                    output_button.setEnabled(not active)
                    layout.addWidget(output_button)
            attrs = self.catalog.attributes(node["shader"])
            preferred = {"albedo", "roughness", "metallic", "specular", "show_diffuse", "show_specular", "show_clearcoat", "clearcoat", "clearcoat_roughness", "show_emission", "emission", "input_normal", "input_normal_dial", "refractive_index"}
            order = self.catalog.ordered_attributes(node["shader"])
            color_ports = ramp_color_inputs(self.catalog, node["shader"])
            order = [name for name in order if name not in color_ports]
            if node.get("usd_light"):
                from .light_graph import light_fields
                order = [name for name in light_fields(node["shader"]) if name in attrs]
            destinations = {}
            groups = self.catalog.parameter_groups(node["shader"], order)
            group_order = {"fuzz": 1, "glitter": 2}
            if node["shader"] == "DwaMetalMaterial":
                group_order["specular"] = -1
            groups.sort(key=lambda group: group_order.get(group[0].casefold().split(" ", 1)[0], 0))
            for group_name, members in groups:
                group_title = "Clear Coat" if group_name == "Clearcoat" else group_name
                if group_title:
                    section_key = (node_id, group_name)
                    box = CollapsibleSection(group_title, expanded=doc.parameter_sections.get(section_key, False))
                    box.setObjectName("parameter_group_" + group_name.lower().replace(" ", "_"))
                    box.header.setObjectName(box.objectName() + "_toggle")
                    box.content.setObjectName(box.objectName() + "_content")
                    box.expanded_changed.connect(lambda expanded, key=section_key, d=doc:
                                                 d.parameter_sections.__setitem__(key, expanded))
                    group_layout = QGridLayout(box.content)
                else:
                    box = QWidget()
                    group_layout = QGridLayout(box)
                group_layout.setContentsMargins(2, 2, 2, 2)
                group_layout.setSpacing(2)
                group_layout.setColumnStretch(0, 1)
                group_layout.setColumnStretch(1, 3)
                layout.addWidget(box)
                self.parameter_groups.append(box)
                for name in members:
                    destinations[name] = (group_title, group_layout)

            def add_parameter_row(grid, row, name, text, tooltip=""):
                title = label(text)
                title.setObjectName("parameter_label_" + name)
                title.setTextFormat(Qt.TextFormat.PlainText)
                title.setWordWrap(True)
                title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                title.setMinimumWidth(80)
                title.setMaximumWidth(140)
                title.setMinimumHeight(24)
                title.setToolTip(tooltip or text)
                row.setObjectName("parameter_row_" + name)
                # Keep compact numeric values legible when colors or vectors
                # share the narrower field column. Very narrow panels can scroll.
                for field in row.findChildren(NumericSpinBox):
                    field.ensurePolished()
                    field.setMinimumWidth(max(field.minimumWidth(),
                                              field.fontMetrics().horizontalAdvance("-0.0000") + 12))
                index = grid.rowCount()
                grid.addWidget(title, index, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(row, index, 1, Qt.AlignmentFlag.AlignTop)
                self.parameter_labels[name] = title

            order = list(destinations)
            ramps = color_ramps(self.catalog, node["shader"])
            ramp_members = {name: ramp for ramp, fields in ramps.items() for name in fields.values()
                            if set(fields.values()) <= set(order)}
            handled_ramps = set()
            grades = channel_groups(self.catalog, node["shader"])
            grade_members = {name: base for base, group in grades.items() for name in group["channels"]
                             if set(group["channels"]) <= set(order)}
            handled_grades = set()
            for attr_name in order:
                attr = attrs[attr_name]
                grade = grade_members.get(attr_name)
                if grade:
                    if grade in handled_grades:
                        continue
                    handled_grades.add(grade)
                    group = grades[grade]
                    fields = group["channels"]
                    inputs = []
                    for name in fields:
                        incoming = self.graph.incoming(node_id, name)
                        source = (self.graph.node(incoming["source"])["label"] + "." + incoming.get("output", "out")) if incoming else ""
                        inputs.append(dict(visible=name in node["ports"], source=source))
                    row = QWidget()
                    rows = QVBoxLayout(row)
                    rows.setContentsMargins(0, 0, 0, 0)
                    rows.setSpacing(2)
                    editor = ColorGradeEditor([self.graph.value(node, name) for name in fields], inputs=inputs)
                    editor.setObjectName("parameter_" + grade + "_rgb")
                    editor.setEnabled(bool(self.graph.value(node, group["switch"])))
                    if not editor.isEnabled():
                        editor.setToolTip("Enable '" + group["switch"].replace("_", " ") + "' to edit these channels.")
                    editor.changed.connect(lambda values, n=node_id, g=grade, names=fields, d=doc:
                                           self.set_grade_parameters(n, g, dict(zip(names, values)), d))
                    editor.input_toggled.connect(lambda index, n=node_id, names=fields, d=doc:
                                                 self.toggle_port(n, names[index], document=d))
                    editor.input_disconnected.connect(lambda index, n=node_id, names=fields, d=doc:
                        self.apply("Disconnect grading channel", lambda graph: graph.disconnect(n, names[index]), document=d))
                    rows.addWidget(editor)
                    group_title, group_layout = destinations[attr_name]
                    row.setProperty("parameter_search", " ".join((grade, "RGB", group_title, *fields)))
                    add_parameter_row(group_layout, row, grade + "_rgb", grade + " RGB",
                                      attrs[grade].get("metadata", {}).get("comment", grade))
                    self.parameter_rows.append((grade + "_rgb", True, row))
                    continue
                is_key = node["shader"] != "DwaBaseMaterial" or attr_name in preferred or attr_name in node["values"] or attr_name in node["ports"]
                ramp = ramp_members.get(attr_name)
                ramp_error = ""
                if ramp:
                    if ramp in handled_ramps:
                        continue
                    fields = ramps[ramp]
                    values = {name: self.graph.value(node, name) for name in fields.values()}
                    try:
                        read_stops(values[fields["values"]], values[fields["positions"]], values[fields["interpolation_types"]])
                    except ValueError as exc:
                        # Keep the original arrays editable so an imported,
                        # malformed ramp can be repaired without discarding data.
                        ramp_error = str(exc)
                    else:
                        handled_ramps.add(ramp)
                        row = QWidget()
                        rows = QVBoxLayout(row)
                        rows.setContentsMargins(0, 0, 0, 0)
                        rows.setSpacing(2)
                        title = ramp.replace("_", " ").capitalize()
                        space_name = "color_space" if "color_space" in attrs else ramp + "_interpolation_mode"
                        space = self.graph.value(node, space_name) if space_name in attrs else 0
                        four_corner = "ramp_type" in attrs and self.graph.value(node, "ramp_type") == 7
                        color_inputs = None
                        if color_ports:
                            color_inputs = []
                            for port in color_ports[:len(values[fields["values"]])]:
                                incoming = self.graph.incoming(node_id, port)
                                source = (self.graph.node(incoming["source"])["label"] + "." + incoming.get("output", "out")) if incoming else ""
                                color_inputs.append(dict(visible=port in node["ports"], source=source))
                        editor = ColorRampEditor(fields, values, selected=doc.ramp_selections.get((node_id, ramp), 0),
                                                 space=space, four_corner=four_corner,
                                                 color_mode=doc.color_modes.get((node_id, fields["values"]), "RGB"),
                                                 color_inputs=color_inputs)
                        editor.setObjectName("parameter_" + ramp)
                        editor.selection_changed.connect(lambda index, key=(node_id, ramp), d=doc: d.ramp_selections.__setitem__(key, index))
                        editor.color_mode_changed.connect(lambda mode, key=(node_id, fields["values"]), d=doc: d.color_modes.__setitem__(key, mode))
                        editor.changed.connect(lambda values, n=node_id, r=ramp, d=doc, e=editor: self.set_ramp_parameters(n, r, values, d, e.last_order))
                        editor.input_toggled.connect(lambda index, n=node_id, d=doc, ports=color_ports: self.toggle_port(n, ports[index], document=d))
                        editor.input_disconnected.connect(lambda index, n=node_id, d=doc, ports=color_ports: self.apply(
                            "Disconnect ramp color", lambda g: g.disconnect(n, ports[index]), document=d))
                        rows.addWidget(editor)
                        group_title, group_layout = destinations[attr_name]
                        row.setProperty("parameter_search", " ".join([title, ramp, group_title, *fields.values(),
                            *(attrs[name].get("metadata", {}).get("label", name) for name in fields.values())]))
                        add_parameter_row(group_layout, row, ramp, title)
                        is_key = node["shader"] != "DwaBaseMaterial" or any(name in node["values"] or name in node["ports"] for name in fields.values())
                        self.parameter_rows.append((ramp, is_key, row))
                        continue
                row = QWidget()
                rows = QVBoxLayout(row)
                rows.setContentsMargins(0, 0, 0, 0)
                rows.setSpacing(2)
                if ramp_error:
                    warning = label(ramp_error + " Edit the arrays below to repair the ramp.", "muted")
                    warning.setWordWrap(True)
                    rows.addWidget(warning)
                connection = self.graph.incoming(node_id, attr_name)
                editor = (self.parameter_editor(node_id, attr_name, attr, self.graph.value(node, attr_name))
                          if not connection and editable(attr) else None)
                if editor is not None and attr_name in grades and self.graph.value(node, grades[attr_name]["switch"]):
                    editor.setEnabled(False)
                    editor.setToolTip("Per-channel correction is enabled. Edit the RGB controls, or turn off '" +
                                      grades[attr_name]["switch"].replace("_", " ") + "' to use this uniform value.")
                if (editor is not None and node.get("usd_camera", {}).get("follow", True)
                        and "usd_camera" in node and attr_name in self.camera_graphs.controlled_values):
                    editor.setEnabled(False)
                    editor.setToolTip("Controlled by the USD camera. Uncheck Follow USD camera to edit this value.")
                if (editor is not None and node.get("usd_transform") and node["usd_transform"].get("follow", True)
                        and attr_name in {"projection_matrix", "projection_mode"}):
                    editor.setEnabled(False)
                    editor.setToolTip("Controlled by the USD transform. Uncheck Follow USD Xform to edit this value.")
                title_text = attr.get("metadata", {}).get("label", attr_name).replace("_", " ")
                group_title, group_layout = destinations[attr_name]
                row.setProperty("parameter_search", " ".join((attr_name, title_text, group_title)))
                if isinstance(editor, QCheckBox):
                    editor.setText("")
                    editor.setAccessibleName(title_text)
                value_row = QHBoxLayout()
                value_row.setSpacing(2)
                reveal = None
                if self.catalog.input_kind(attr):
                    reveal = button("●" if attr_name in node["ports"] else "○", lambda checked=False, n=attr_name: self.toggle_port(node_id, n))
                    reveal.setFixedSize(26, 24)
                    reveal.setStyleSheet("padding: 0;")
                    reveal.setToolTip("Show or hide this input socket on the graph")
                    reveal.setObjectName("input_visibility_" + attr_name)
                    reveal.setAccessibleName(("Hide " if attr_name in node["ports"] else "Show ") + title_text + " input")
                if connection:
                    linked = QHBoxLayout()
                    linked.setSpacing(2)
                    sources = [self.graph.node(c["source"])["label"] + "." + c.get("output", "out") for c in self.graph.data["connections"] if c["target"] == node_id and c["input"] == attr_name]
                    linked_label = label("↳ " + ", ".join(sources), "muted")
                    linked_label.setWordWrap(True)
                    linked.addWidget(linked_label, 1)
                    linked.addWidget(button("Unlink", lambda checked=False, n=attr_name: self.disconnect(node_id, n)))
                    value_row.addLayout(linked, 1)
                elif editor is not None:
                    value_row.addWidget(editor, 1)
                else:
                    value_row.addWidget(label("Connect a shader" if self.catalog.input_kind(attr) else "Scene reference · renderer default", "muted"), 1)
                if reveal:
                    value_row.addWidget(reveal, 0, Qt.AlignmentFlag.AlignTop)
                rows.addLayout(value_row)
                if attr_name == "projection_matrix" and projection_matrix_mode(self.catalog, node["shader"]) is not None:
                    link = button("Link USD Xform…", lambda checked=False, d=doc, n=node_id: self.transform_graphs.choose(d, n))
                    link.setObjectName("link_usd_transform")
                    link.setEnabled(self.transform_graphs.available())
                    link.setToolTip("Use a USD prim's world transform for projection. Opens a searchable list from the current stage.")
                    rows.addWidget(link)
                    reference = node.get("usd_transform")
                    if reference:
                        target = label(reference["path"], "muted")
                        target.setWordWrap(True)
                        target.setToolTip(reference["scene"])
                        rows.addWidget(target)
                        follow = QCheckBox("Auto update")
                        follow.setObjectName("follow_usd_transform")
                        follow.setChecked(reference.get("follow", True))
                        follow.setToolTip("Follow the world transform and animation at the current USD frame. Uncheck to keep the captured matrix and edit it here.")
                        follow.toggled.connect(lambda enabled, d=doc, n=node_id: self.transform_graphs.set_follow(d, n, enabled))
                        follow.setEnabled(self.usd_viewer.ready and reference["scene"] == self.transform_graphs.scene)
                        refresh = button("Refresh from USD Xform", lambda checked=False, d=doc, n=node_id, p=reference["path"]: self.transform_graphs.link(d, n, p))
                        refresh.setObjectName("refresh_usd_transform")
                        refresh.setEnabled(self.transform_graphs.available() and reference["scene"] == self.transform_graphs.scene)
                        refresh.setToolTip("Capture the current USD world transform once, keeping the Auto update setting.")
                        controls = QHBoxLayout()
                        controls.addWidget(refresh, 1)
                        controls.addWidget(follow)
                        rows.addLayout(controls)
                        if follow.isChecked() and (reference["scene"] != self.transform_graphs.scene or reference["path"] not in self.transform_graphs.cache):
                            rows.addWidget(label("Source unavailable · using last captured transform", "muted"))
                        unlink = button("Unlink Xform", lambda checked=False, d=doc, n=node_id: self.transform_graphs.unlink(d, n))
                        unlink.setObjectName("unlink_usd_transform")
                        unlink.setToolTip("Remove the link and keep the captured projection matrix.")
                        rows.addWidget(unlink)
                add_parameter_row(group_layout, row, attr_name, title_text,
                                  attr.get("metadata", {}).get("comment", attr_name))
                self.parameter_rows.append((attr_name, is_key, row))
        layout.addStretch()
        while self.parameter_header.count():
            old_header = self.parameter_header.takeAt(0).widget()
            old_header.hide()
            old_header.deleteLater()
        self.parameter_header.addWidget(header)
        old = self.inspector.takeWidget()
        if old:
            old.deleteLater()
        self.inspector.setWidget(panel)
        polish_tree(header)
        polish_tree(panel)
        self.filter_parameters()
        QTimer.singleShot(0, lambda: self.inspector.verticalScrollBar().setValue(scroll))

    def filter_parameters(self, *args):
        normalize = lambda text: "".join(char for char in text.casefold() if char.isalnum())
        query = normalize(self.parameter_search.text())
        visible_groups = set()
        for name, is_key, widget in self.parameter_rows:
            visible = (not query or query in normalize(widget.property("parameter_search"))) and (bool(query) or is_key or self.all_parameters.isChecked())
            widget.setVisible(visible)
            self.parameter_labels[name].setVisible(visible)
            if visible:
                visible_groups.add(widget.parentWidget())
        for group in self.parameter_groups:
            if isinstance(group, CollapsibleSection):
                group.set_search_active(bool(query))
                group.setVisible(group.content in visible_groups)
            else:
                group.setVisible(group in visible_groups)

    def parameter_editor(self, node_id, name, attr, value):
        t = attr["attrType"]
        doc = self.document
        commit = lambda v: self.set_parameter(node_id, name, v, doc)
        if attr.get("enum"):
            widget = QComboBox()
            for text, number in sorted(attr["enum"].items(), key=lambda pair: pair[1]):
                widget.addItem(text, number)
            widget.setCurrentIndex(widget.findData(value))
            widget.currentIndexChanged.connect(lambda _: commit(widget.currentData()))
        elif t == "Bool":
            widget = QCheckBox(attr.get("metadata", {}).get("label", name).replace("_", " "))
            widget.setToolTip(attr.get("metadata", {}).get("comment", name))
            widget.setChecked(value)
            widget.toggled.connect(commit)
        elif t in {"Int", "Float", "Double"}:
            widget = QSpinBox() if t == "Int" else NumericSpinBox()
            if t == "Int":
                widget.setRange(-2147483648, 2147483647)
            else:
                widget.set_numeric_type("double" if t == "Double" else "float")
                widget.setRange(-1e12, 1e12)
                widget.setSingleStep(0.05)
            widget.setValue(value)
            widget.setFixedHeight(24)
            widget.setStyleSheet("padding: 1px 5px;")
            widget.setKeyboardTracking(False)
            widget.editingFinished.connect(lambda: commit(widget.value()))
        elif node_id and is_rgb_grade(doc.graph.node(node_id)["shader"], name, attr):
            widget = ColorGradeEditor(value)
            widget.changed.connect(lambda values: self.set_grade_parameters(node_id, name, {name: values}, doc))
        elif t == "Rgb":
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            components = ColorComponents(value, doc.color_modes.get((node_id, name), "RGB"))
            components.mode_changed.connect(lambda mode, key=(node_id, name), d=doc: d.color_modes.__setitem__(key, mode))
            components.changed.connect(commit)
            row.addWidget(components, 1)
            swatch = button("", lambda: self.pick_color(components.value(), commit))
            swatch.setFixedSize(24, 24)
            def update_swatch(rgb):
                color = QColor.fromRgbF(*display_color(rgb))
                swatch.setStyleSheet(f"background: {color.name()}; border: 1px solid #7c8c96;")
            update_swatch(value)
            components.value_changed.connect(update_swatch)
            swatch.setToolTip("Pick a color with the visual HSV selector")
            row.addWidget(swatch)
        elif t in VECTOR_TYPES:
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            fields = []
            for v in value:
                spin = NumericSpinBox()
                spin.set_numeric_type("double" if t.endswith("d") else "float")
                spin.setRange(-1e12, 1e12)
                spin.setSingleStep(0.05)
                spin.setMinimumWidth(30)
                spin.setFixedHeight(24)
                spin.setStyleSheet("padding: 1px 5px;")
                spin.setValue(v)
                spin.setKeyboardTracking(False)
                fields.append(spin)
                row.addWidget(spin, 1)
                spin.editingFinished.connect(lambda: commit([f.value() for f in fields]))
        else:
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setSpacing(2)
            row.setContentsMargins(0, 0, 0, 0)
            field = QLineEdit(value) if t == "String" else NumericLineEdit(json.dumps(value), json_value=True)
            field.setToolTip("JSON array" if t.endswith("Vector") else "Value")
            row.addWidget(field)
            def accept():
                try:
                    commit(field.text() if t == "String" else json.loads(field.text()))
                except ValueError as exc:
                    self.error(exc)
            field.editingFinished.connect(accept)
            if attr.get("filename"):
                def browse():
                    path, _ = QFileDialog.getOpenFileName(self, "Choose texture", str(LOCAL_ROOT))
                    if path:
                        commit(path)
                browse_button = button("…", browse)
                browse_button.setFixedWidth(28)
                browse_button.setToolTip("Choose texture file")
                set_local_style(browse_button, "padding: 1px 2px;", "padding: 1px 2px;", explorer="")
                row.addWidget(browse_button)
                field.setToolTip(str(value))
                field.textChanged.connect(field.setToolTip)
                tx_status = None
                if node_id and texture_attribute(doc.graph.node(node_id)["shader"], name, attr, value):
                    tx = QCheckBox("Use .tx")
                    tx.setObjectName("texture_tx_" + name)
                    tx.setChecked(name in doc.graph.node(node_id).get("tx_textures", []))
                    tx.setToolTip("Convert this image to a tiled, mipmapped .tx in the background and use the cached file "
                                  "for rendering and export. The source stays unchanged. Set the cache folder in Application Settings. "
                                  "Uncheck to use the original; uncheck and check again to retry a failed conversion.")
                    tx.toggled.connect(lambda enabled: self.set_texture_conversion(node_id, name, enabled, doc))
                    row.addWidget(tx)
                    if tx.isChecked():
                        entry = self.texture_conversions.request(value, doc.path.parent if doc.path else EDITOR_ROOT)
                        tx_status = TextureConversionStatus(self.texture_conversions, entry)
                preview = None
                if node_id and any(field_name == name for field_name, _, _ in texture_fields(doc.graph, doc.graph.node(node_id))):
                    raw = "normal" in (doc.graph.node(node_id)["shader"] + name).lower()
                    preview = TexturePreviewLabel(self.texture_previews, self.texture_previews.request(
                        value, doc.path.parent if doc.path else EDITOR_ROOT, raw))
                if preview is not None or tx_status is not None:
                    # Keep path, browse and conversion switch on the same row.
                    container = QWidget()
                    column = QVBoxLayout(container)
                    column.setContentsMargins(0, 0, 0, 0)
                    column.addWidget(widget)
                    if tx_status is not None:
                        column.addWidget(tx_status)
                    if preview is not None:
                        column.addWidget(preview)
                    widget = container
        widget.setObjectName("parameter_" + name)
        return widget

    def pick_color(self, value, commit):
        initial = QColor.fromRgbF(*display_color(value))
        color = NormalizedColorDialog.getColor(initial, self, "Choose color (HSV / RGB)")
        if color.isValid() and color != initial:
            commit(linear_color((color.redF(), color.greenF(), color.blueF())))

    def confirm_discard(self):
        if not self.commit_active_edits():
            return False
        if self.undo.isClean():
            return True
        answer = QMessageBox.question(self, "Unsaved graph", f"Save changes to {self.graph.data['name']}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save_graph()
        return answer == QMessageBox.StandardButton.Discard

    def new_graph(self):
        graph = Graph(self.catalog)
        names = {doc.graph.data["name"] for doc in self.documents}
        name, number = "Untitled material", 2
        while name in names:
            name = f"Untitled material {number}"
            number += 1
        graph.rename_material(name)
        graph.add("DwaBaseMaterial")
        self.add_document(graph)
        self.activity_log.record("Application", "Created material: " + name)

    def open_graph(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open shader graph", str(self.path.parent if self.path else EDITOR_ROOT / "examples"), "MoonRay graph (*.moonraygraph);;JSON (*.json)")
        if not path:
            return
        self.load_graph_file(path)

    def open_example(self, filename):
        self.load_graph_file(EDITOR_ROOT / "examples" / filename)

    def load_graph_file(self, path):
        path = Path(path).resolve()
        for index, doc in enumerate(self.documents):
            if doc.path and doc.path.resolve() == path:
                self.graph_tabs.setTabVisible(index, True)
                self.graph_tabs.setCurrentIndex(index)
                self.switch_document(index)
                self.show_material_editor()
                self.recent_scenes.remember("graph", path)
                return
        try:
            graph = Graph.load(self.catalog, path)
        except (OSError, ValueError) as exc:
            self.error("Could not open graph: " + str(exc))
            QMessageBox.warning(self, "Cannot open graph", str(exc))
            return
        self.add_document(graph, path)
        self.recent_scenes.remember("graph", path)
        self.activity_log.record("Application", "Opened graph: " + str(path))

    def save_graph(self, save_as=False):
        if self.document not in self.documents:
            self.error("Select or create a material graph first.")
            return False
        if not self.commit_active_edits():
            return False
        path = self.path
        if path is None or save_as:
            filename, _ = QFileDialog.getSaveFileName(self, "Save shader graph", str(self.default_export_path(".moonraygraph")), "MoonRay graph (*.moonraygraph)")
            if not filename:
                return False
            path = Path(filename)
            if not path.suffix:
                path = path.with_suffix(".moonraygraph")
        try:
            if any(doc is not self.document and doc.path and doc.path.resolve() == path.resolve() for doc in self.documents):
                raise GraphError("This file is already open in another material tab. Choose a different filename.")
            self.graph.save(path)
        except (OSError, ValueError) as exc:
            self.error("Could not save graph: " + str(exc))
            QMessageBox.warning(self, "Cannot save graph", str(exc))
            return False
        self.path = path
        self.undo.setClean()
        self.update_title()
        self.statusBar().showMessage(f"Saved {path}", 8000)
        self.recent_scenes.remember("graph", path)
        return True

    def auto_changed(self, enabled):
        if enabled:
            self.schedule_render()
        else:
            self.timer.stop()
            self.pending_render = False

    def schedule_render(self, *args):
        if self.auto.isChecked() and self.document in self.documents and self.document.editor_open:
            self.timer.start()

    def render_settings_changed(self, *args):
        self.document.mode, self.document.size = self.mode.currentData(), self.resolution.currentData()
        self.revision += 1
        self.document_revision += 1
        self.preview_status.setText("Render settings changed · preview out of date")
        self.schedule_render()

    def render_or_cancel(self):
        if self.process:
            self.cancel_job()
        else:
            self.render()

    def render(self):
        if self.document not in self.documents:
            self.error("Select or create a material graph first.")
            return
        self.timer.stop()
        if self.process:
            self.pending_render = True
            return
        self.start_job("render")

    def export_usd(self, *, preview_scene=False):
        if self.document not in self.documents:
            self.error("Select or create a material graph first.")
            return
        if not self.commit_active_edits():
            return
        if self.process:
            self.statusBar().showMessage("Wait for the current render or cancel it before exporting.", 7000)
            return
        title = "Save material preview scene USD" if preview_scene else "Save material USD"
        suffix = "_preview.usda" if preview_scene else ".usda"
        path, selected = QFileDialog.getSaveFileName(self, title, str(self.default_export_path(suffix)),
                                                   "USD ASCII (*.usda);;USD binary (*.usdc)")
        if path:
            target = Path(path).with_suffix(".usdc" if selected == "USD binary (*.usdc)" else ".usda")
            # QFileDialog only confirmed the entered filename, which may differ
            # after applying the chosen format's extension.
            if target != Path(path) and target.exists() and QMessageBox.question(self, "Replace USD file?",
                f"Replace {target}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.start_job("save-preview" if preview_scene else "export", target)

    def export_rdl(self):
        self.export_active_scene()

    def copy_preview_to_usd(self):
        if not self.commit_active_edits():
            return
        if self.process:
            self.statusBar().showMessage("Wait for the current render or cancel it before copying the preview scene.", 7000)
            return
        directory = CACHE_ROOT / "preview-scenes" / uuid.uuid4().hex
        self.start_job("copy-preview", directory / (self.graph.material_export_name() + "_preview.usda"))

    def render_in_moonray_gui(self):
        if self.process:
            self.statusBar().showMessage("Wait for the current render or cancel it before opening moonray_gui.", 7000)
            return
        if not self.commit_active_edits():
            return
        self.timer.stop()
        directory = CACHE_ROOT / "moonray-gui" / uuid.uuid4().hex
        self.start_job("moonray-gui", directory)

    def export_to_moonray_gui(self):
        self.export_active_scene(launch=True)

    def export_active_scene(self, *, launch=False):
        if self.document not in self.documents:
            self.error("Select or create a material graph first.")
            return
        if self.process:
            self.statusBar().showMessage("Wait for the current operation or cancel it before exporting the scene.", 7000)
            return
        if not self.commit_active_edits():
            return
        suggested = self.default_export_path(".rdla")
        title = "Export to RDLA and render with moonray_gui" if launch else "Export scene RDL · Material preview"
        path, _ = QFileDialog.getSaveFileName(self, title, str(suggested),
                                             "MoonRay scene (*.rdla)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        target = Path(path).expanduser().absolute().with_suffix(".rdla")
        if target.exists() and QMessageBox.question(self, "Replace RDLA file?", f"Replace {target}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.timer.stop()
        self.start_job("export-rdl-gui" if launch else "export-rdl", target)

    def start_job(self, action, output=None, raise_errors=False):
        try:
            if self.document not in self.documents:
                raise ValueError("Select or create a material graph first.")
            self.graph.validate(require_surface=True)
            region = self.preview.selection.active if action == "render" else None
            if region and self.preview.original.width() != self.resolution.currentData():
                raise ValueError("The preview resolution changed. Clear the region and render a full preview first.")
            region = validate_region(region, self.resolution.currentData(), self.resolution.currentData())
            self.document.preview_region = (self.preview.selection.enabled, self.preview.selection.region)
            cache = CACHE_ROOT / "renders"
            cache.mkdir(parents=True, exist_ok=True)
            self.job_dir = tempfile.TemporaryDirectory(prefix="job-", dir=cache)
            directory = Path(self.job_dir.name)
            self.graph.save(directory / "graph.moonraygraph")
            if region and self.document.preview_dir:
                shutil.copyfile(Path(self.document.preview_dir.name) / "preview.exr", directory / "region_base.exr")
                shutil.copyfile(Path(self.document.preview_dir.name) / "preview.png", directory / "region_base.png")
        except (OSError, ValueError) as exc:
            if self.job_dir:
                self.job_dir.cleanup()
                self.job_dir = None
            if raise_errors:
                raise
            self.error(exc)
            return
        self.cancelled = False
        self.pending_render = False
        self.job_document = self.document
        self.job_log = LogStream()
        self.job_revision = self.revision
        self.job_action = action
        self.job_region = region
        self.job_id = uuid.uuid4().hex
        self.job_output = output or directory
        self.started = time.monotonic()
        self.log.clear()
        self.log.appendPlainText(f"{action.title()} · {self.graph.data['name']}")
        self.document.log = self.log.toPlainText()
        process = QProcess(self)
        self.process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.finish_job)
        process.errorOccurred.connect(self.process_error)
        from .maya_host import job_command
        args = job_command("worker") + [action, str(directory / "graph.moonraygraph"), str(self.job_output),
                "--size", str(self.resolution.currentData()), "--mode", self.mode.currentData(),
                "--progress-step", str(self.progress_step.currentData())]
        args += ["--base-dir", str(self.document.path.parent if self.document.path else EDITOR_ROOT)]
        if region:
            args += ["--region", *map(str, region)]
            if (directory / "region_base.exr").exists():
                args += ["--region-base", str(directory / "region_base.exr")]
                args += ["--region-base-image", str(directory / "region_base.png")]
        self.progress_step.setEnabled(False)
        self.render_button.setText("Cancel")
        if action == "render":
            self.document.render_percent = 0
            self.preview_progress.setValue(0)
            self.preview_status.setText("Rendering with MoonRay…")
            self.document.preview_status = self.preview_status.text()
        self.statusBar().showMessage(f"{action.title()} in progress…")
        self.job_started.emit(self.job_id)
        document = self.job_document
        def ready():
            if self.process is not process:
                return
            try:
                document.graph.save(directory / "graph.moonraygraph")
                self.job_revision = document.revision
                # macOS has no setsid(1): run-job.sh starts the worker in its own
                # session, so cancel_job can signal the whole process group.
                process.start(args[0], args[1:])
            except (OSError, ValueError) as exc:
                failed(str(exc))
        def failed(message):
            if self.process is process:
                self.log.appendPlainText(str(message))
                self.finish_job(1, QProcess.ExitStatus.NormalExit)
        self.refresh_scene_links(ready, failed)
        return self.job_id

    def read_output(self, final=False):
        if self.process:
            output = self.job_log.feed(bytes(self.process.readAllStandardOutput()), final=final)
            if output:
                doc = self.job_document
                doc.log = "\n".join((doc.log + "\n" + output).splitlines()[-2500:])
                if self.job_action == "render":
                    doc.render_percent = logged_percent(output, doc.render_percent)
                if doc is self.document:
                    self.preview_progress.setValue(doc.render_percent)
                    self.log.appendPlainText(output)
                else:
                    self.activity_log.record("Material · " + doc.graph.data["name"], output)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.error("Could not start the MoonRay worker")
            self.finish_job(-1, QProcess.ExitStatus.CrashExit)

    def finish_job(self, code, status):
        if self.process is None:
            return
        self.read_output(final=True)
        elapsed = time.monotonic() - self.started
        success = code == 0 and not self.cancelled
        doc = self.job_document
        if success:
            if self.job_action == "render":
                if self.job_revision == doc.revision:
                    try:
                        image = QPixmap(str(self.job_output / "preview.png"))
                        if image.isNull():
                            raise GraphError("Could not load the rendered preview")
                        doc.image = image
                        doc.preview_dir, self.job_dir = self.job_dir, None
                        doc.preview_revision = self.job_revision
                        view = "display filter" if doc.graph.data["display"] else "render output" if doc.graph.data["aov"] else doc.graph.data["preview"]["render_view"]
                        doc.preview_status = f"{image.width()} × {image.height()} px · {elapsed:.1f}s · {view} preview"
                        if self.job_region:
                            doc.preview_status += " · region updated"
                    except GraphError as exc:
                        doc.log += "\nERROR: " + str(exc)
                        self.activity_log.record("Material · " + doc.graph.data["name"], "ERROR: " + str(exc))
                        doc.preview_status = "Render failed · see Render log"
                        success = False
                else:
                    doc.preview_status = "Material changed · preview out of date"
            result = ("Opened in moonray_gui · " + str(self.job_output) if self.job_action in ("moonray-gui", "export-rdl-gui") else
                      "Preview ready" if self.job_action == "render" else "Exported " + str(self.job_output))
            self.statusBar().showMessage(f"{doc.graph.data['name']} · {result} · {elapsed:.1f}s", 10000)
        elif self.cancelled:
            doc.preview_status = "Render cancelled"
            self.statusBar().showMessage("Cancelled", 5000)
        else:
            doc.preview_status = "Could not open moonray_gui · see Render log" if self.job_action in ("moonray-gui", "export-rdl-gui") else "Render failed · see Render log"
            if doc is self.document:
                self.tabs.setCurrentWidget(self.log_page)
            self.statusBar().showMessage("MoonRay job failed. See the render log for details.")
        if self.job_action == "render":
            doc.render_percent = 0
        if doc is self.document:
            self.show_document_preview()
        self.process.deleteLater()
        self.process = None
        self.render_button.setText("Render preview")
        self.progress_step.setEnabled(True)
        if self.job_dir:
            self.job_dir.cleanup()
            self.job_dir = None
        again = self.pending_render and self.auto.isChecked() and not self.cancelled
        self.pending_render = False
        self.job_finished.emit(success)
        if success and self.job_action == "copy-preview":
            output = self.job_output
            QTimer.singleShot(0, lambda: self.usd_viewer.open_file(output) if doc in self.documents else None)
        if again:
            self.timer.start(150)

    def cancel_job(self, *, wait=False):
        self.timer.stop()
        self.pending_render = False
        if not self.process:
            return
        process = self.process
        self.cancelled = True
        pid = self.process.processId()
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                self.process.terminate()
        else:
            if self.process.state() == QProcess.ProcessState.NotRunning:
                self.finish_job(1, QProcess.ExitStatus.NormalExit)
            else:
                self.process.kill()
        if wait and process.state() != QProcess.ProcessState.NotRunning and not process.waitForFinished(2000):
            pid = process.processId()
            if pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill()
            process.waitForFinished(1000)

    def confirm_close(self):
        """Ask to save each changed graph; False keeps the editor open."""
        for index in range(len(self.documents)):
            self.graph_tabs.setCurrentIndex(index)
            self.switch_document(index)
            if self.document is not self.documents[index] or not self.confirm_discard():
                return False
        return True

    def shutdown(self):
        """Stop jobs and background work; called when the editor is deleted or Maya quits."""
        process = self.process
        self.cancel_job()
        if process and not process.waitForFinished(2000):
            pid = process.processId()
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.kill()
            process.waitForFinished(1000)
        self.ramp_bakes.shutdown()
        self.light_graphs.shutdown()
        self.usd_viewer.shutdown()
        self.texture_previews.close()
        self.texture_conversions.close()
        for doc in self.documents:
            doc.preview_dir = None
