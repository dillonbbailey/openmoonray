"""Compact parameter sections with temporary expansion for search results."""
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QFrame, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    expanded_changed = Signal(bool)

    def __init__(self, title, *, expanded=False, parent=None):
        super().__init__(parent)
        self._expanded = expanded
        self._search_active = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setAccessibleName(title + " parameters")
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.header.setMinimumHeight(24)
        self.header.setStyleSheet("QToolButton { text-align: left; padding: 2px 4px; }")
        self.header.toggled.connect(self._toggled)
        layout.addWidget(self.header)
        self.content = QFrame()
        self.content.setFrameShape(QFrame.Shape.StyledPanel)
        layout.addWidget(self.content)
        self._show_expanded(expanded)

    def _show_expanded(self, expanded):
        with QSignalBlocker(self.header):
            self.header.setChecked(expanded)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.header.setToolTip(("Collapse " if expanded else "Expand ") + self.header.text())
        self.content.setVisible(expanded)

    def _toggled(self, expanded):
        self._show_expanded(expanded)
        if not self._search_active:
            self._expanded = expanded
            self.expanded_changed.emit(expanded)

    def set_search_active(self, active):
        """Reveal matches while searching, then restore the user's section state."""
        if active != self._search_active:
            self._search_active = active
            self._show_expanded(True if active else self._expanded)
