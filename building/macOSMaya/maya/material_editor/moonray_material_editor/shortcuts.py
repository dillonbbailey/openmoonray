"""Named host shortcuts shared by preferences, menus and focused widgets."""
import json

from PySide6.QtCore import QObject, Slot, Qt
from PySide6.QtGui import QKeySequence

# ID, visible command, context, primary key, alternate key.
SHORTCUTS = (
    ("project.new", "New project", "Application", "Ctrl+Alt+N", ""),
    ("project.open", "Open project", "Application", "Ctrl+Alt+O", ""),
    ("project.save", "Save project", "Application", "Ctrl+Alt+S", ""),
    ("project.save_as", "Save project as", "Application", "Ctrl+Alt+Shift+S", ""),
    ("material.new", "New material", "Application", "Ctrl+N", ""),
    ("graph.open", "Open graph", "Application", "Ctrl+O", ""),
    ("graph.save", "Save graph", "Application", "Ctrl+S", ""),
    ("graph.save_as", "Save graph as", "Application", "Ctrl+Shift+S", ""),
    ("material.close", "Close material tab", "Application", "Ctrl+W", ""),
    ("material.export", "Export material USD", "Application", "Ctrl+E", ""),
    ("scene.export", "Export scene RDL", "Application", "Ctrl+Shift+E", ""),
    ("usd.open", "Open USD stage", "Application", "Ctrl+Shift+O", ""),
    ("edit.undo", "Undo", "Application", "Ctrl+Z", ""),
    ("edit.redo", "Redo", "Application", "Ctrl+Shift+Z", "Ctrl+Y"),
    ("usd.frame", "Frame selection", "USD Viewer", "F", ""),
    ("hierarchy.toggle_active", "Toggle prim activation", "Prim hierarchy", "D", ""),
    ("hierarchy.select", "Selection tool", "Prim hierarchy", "Q", ""),
    ("hierarchy.translate", "Move tool", "Prim hierarchy", "W", ""),
    ("hierarchy.orient", "Rotate tool", "Prim hierarchy", "E", ""),
    ("hierarchy.scale", "Scale tool", "Prim hierarchy", "R", ""),
    ("graph.copy", "Copy nodes", "Material graph", "Ctrl+C", ""),
    ("graph.paste", "Paste nodes", "Material graph", "Ctrl+V", ""),
    ("graph.rename", "Rename node", "Material graph", "F2", ""),
    ("graph.delete", "Delete nodes / wires", "Material graph", "Del", "Backspace"),
    ("graph.frame", "Frame graph", "Material graph", "F", ""),
    ("graph.library", "Shader library", "Material graph", "Tab", ""),
    ("python.comment", "Comment selected lines", "Python editor", "Ctrl+/", ""),
)
DEFAULT_SHORTCUTS = {key: [primary, alternate] for key, _, _, primary, alternate in SHORTCUTS}
SETTINGS_KEY = "application/shortcuts"


def normalize_shortcuts(values):
    if not isinstance(values, dict) or set(values) != set(DEFAULT_SHORTCUTS):
        raise ValueError("Invalid shortcut mapping.")
    result, used = {}, []
    for key, title, context, *_ in SHORTCUTS:
        entries = values[key]
        if not isinstance(entries, (list, tuple)) or len(entries) != 2:
            raise ValueError("Each command needs a primary and alternate shortcut.")
        result[key] = []
        for value in entries:
            if not isinstance(value, str):
                raise ValueError("Invalid shortcut for " + title)
            sequence = QKeySequence.fromString(value, QKeySequence.SequenceFormat.PortableText)
            normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if value and (sequence.count() != 1 or sequence[0].key() == Qt.Key.Key_unknown):
                raise ValueError("Invalid shortcut for " + title)
            if normalized:
                for other, other_title, other_context in used:
                    overlaps = (context == other_context or "Application" in (context, other_context)
                                or {context, other_context} == {"USD Viewer", "Prim hierarchy"})
                    if overlaps and (sequence.matches(other) != QKeySequence.SequenceMatch.NoMatch
                                     or other.matches(sequence) != QKeySequence.SequenceMatch.NoMatch):
                        raise ValueError(f"{normalized} conflicts with {other_title}. Choose another shortcut.")
                used.append((sequence, title, context))
            result[key].append(normalized)
    return result


def load_shortcuts(store):
    try:
        saved = json.loads(store.value(SETTINGS_KEY, "{}"))
        return normalize_shortcuts({key: saved.get(key, list(value)) for key, value in DEFAULT_SHORTCUTS.items()})
    except (TypeError, ValueError, AttributeError):
        return {key: list(value) for key, value in DEFAULT_SHORTCUTS.items()}


def sequences(command):
    from .application_settings import preferences
    return [QKeySequence(value) for value in preferences().shortcuts[command] if value]


class ShortcutBinding(QObject):
    def __init__(self, target, command):
        super().__init__(target)
        from .application_settings import preferences
        self.target, self.command = target, command
        preferences().changed.connect(self.update)
        self.update()

    @Slot()
    def update(self):
        keys = sequences(self.command)
        if hasattr(self.target, "setShortcuts"):
            self.target.setShortcuts(keys)
        else:
            self.target.setKeys(keys)


def bind_shortcut(target, command):
    return ShortcutBinding(target, command)


def matches_shortcut(event, command):
    pressed = QKeySequence(event.keyCombination())
    return any(pressed.matches(sequence) == QKeySequence.SequenceMatch.ExactMatch for sequence in sequences(command))
