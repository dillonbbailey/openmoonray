"""State owned by one material tab, independent of the shared studio panels."""
from pathlib import Path
import uuid

from PySide6.QtGui import QPixmap, QUndoStack

from .canvas import GraphView
from .model import EDITOR_ROOT


class GraphDocument:
    def __init__(self, graph, parent, path=None):
        self.id = uuid.uuid4().hex
        self.editor_open = True
        self.graph = graph
        self.path = Path(path) if path else None
        self.selected = None
        self.usd_shader_link = None  # Live USD edit session; saved graphs remain portable.
        self.undo = QUndoStack(parent)
        self.undo.setUndoLimit(100)
        self.canvas = GraphView()
        self.canvas.texture_previews = parent.texture_previews
        self.canvas.texture_base = lambda: self.path.parent if self.path else EDITOR_ROOT
        parent.texture_previews.updated.connect(self.canvas.texture_ready)
        self.canvas.destroyed.connect(lambda _=None, owner=id(self.canvas): parent.texture_previews.release_maps(owner))
        self.revision = 0
        self.preview_revision = None
        self.add_position = None
        self.ramp_selections = {}
        self.color_modes = {}
        self.parameter_sections = {}
        self.mode = "vectorized"
        self.size = 512
        self.image = QPixmap()
        self.preview_view = (True, 1.0, 0.0, 0.0)
        self.preview_region = (False, None)
        self.preview_dir = None
        self.preview_status = "Render a preview to begin."
        self.render_percent = 0
        self.log = ""


def document_property(name):
    """Keep existing graph commands and MCP operations scoped to the active tab."""
    return property(lambda window: getattr(window.document, name),
                    lambda window, value: setattr(window.document, name, value))
