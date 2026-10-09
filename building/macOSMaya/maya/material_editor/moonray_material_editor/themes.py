"""Application appearance, portable QSS resources, and per-widget overrides."""
from pathlib import Path
import json
import os
import re
from weakref import WeakKeyDictionary

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QPalette
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QFileDialog, QFormLayout, QGridLayout,
                              QGroupBox, QLabel, QLayout, QMessageBox, QWidget)
from shiboken6 import isValid

from . import recent_scenes
from .checkbox_style import CHECKBOX_STYLE


THEME_ROOT = Path(__file__).resolve().parents[1] / "assets" / "themes"
THEME_KEY = "appearance/theme"
PATH_KEY = "appearance/stylesheet"
EXPLORER_PALETTE_CLASSES = ("QToolButton", "QCheckBox", "QRadioButton", "QLineEdit", "QLabel",
                            "QGroupBox", "QMenu", "QMenuBar", "QTabBar", "QTextEdit", "QHeaderView")
GROUP_LAYOUT = ("QGroupBox { margin-top: 12px; padding-top: 8px; } "
                "QGroupBox::title { subcontrol-origin: margin; left: 8px; }")
FOUNDATION = "QWidget { font-family: 'DejaVu Sans'; font-size: 12px; }\n" + CHECKBOX_STYLE
# Keep quoted strings intact, including URLs containing comment-like characters.
QSS_TOKENS = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|/\*[\s\S]*?(?:\*/|$)''')
QSS_URL = re.compile(r'''url\(\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^)'"\n]*))\s*\)''', re.I)
LOCAL_STYLES = WeakKeyDictionary()
LABEL_INDENT = 4
LAYOUT_SPACING = 2


def set_layout_spacing(layout, horizontal, vertical=None):
    """Retain an explicit Designer edit across the shared spacing policy."""
    layout.setProperty("lunaticHorizontalSpacing", horizontal)
    layout.setProperty("lunaticVerticalSpacing", horizontal if vertical is None else vertical)
    apply_layout_spacing(layout)


def layout_spacing(layout):
    return tuple(LAYOUT_SPACING if layout.property(name) is None else layout.property(name)
                 for name in ("lunaticHorizontalSpacing", "lunaticVerticalSpacing"))


def set_label_indent(label, indent):
    label.setProperty("lunaticLabelIndent", indent)
    label.setIndent(indent)


def label_indent(label):
    value = label.property("lunaticLabelIndent")
    return LABEL_INDENT if value is None else value


def apply_layout_spacing(layout):
    """Set both grid/form axes and nested layout gaps without changing margins."""
    horizontal, vertical = layout_spacing(layout)
    if isinstance(layout, (QGridLayout, QFormLayout)):
        if layout.horizontalSpacing() != horizontal:
            layout.setHorizontalSpacing(horizontal)
        if layout.verticalSpacing() != vertical:
            layout.setVerticalSpacing(vertical)
    elif layout.spacing() != horizontal:
        layout.setSpacing(horizontal)
    # Nested layouts are QObject children. Avoid wrapping Qt's private layout
    # items: controls may replace those items while their style is changing.
    for child in layout.children():
        if isinstance(child, QLayout):
            apply_layout_spacing(child)


class WidgetAppearancePolicy(QObject):
    """Apply shared spacing, arrowless spin boxes and theme-specific popups."""
    def eventFilter(self, obj, event):
        kind = event.type()
        if kind not in (QEvent.Type.Polish, QEvent.Type.StyleChange, QEvent.Type.Show,
                        QEvent.Type.LayoutRequest):
            return False
        if isinstance(obj, QWidget) and obj.layout() is not None:
            apply_layout_spacing(obj.layout())
        # LayoutRequest also catches layouts added to already visible widgets.
        # Unchanged spacing is never written, so this does not keep relaying out.
        if kind == QEvent.Type.LayoutRequest:
            return False
        if isinstance(obj, QAbstractSpinBox):
            if obj.buttonSymbols() != QAbstractSpinBox.ButtonSymbols.NoButtons:
                obj.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        elif isinstance(obj, QLabel):
            indent = label_indent(obj)
            if obj.indent() != indent:
                obj.setIndent(indent)
        elif isinstance(obj, QComboBox):
            original = obj.property("lunaticOriginalMaxVisibleItems")
            if self.parent().property("lunaticNeoTheme"):
                # Polish also catches combos created after a theme switch, including
                # Designer forms and controls with a limit set in their constructor.
                if obj.maxVisibleItems() != 2147483647:
                    obj.setProperty("lunaticOriginalMaxVisibleItems", obj.maxVisibleItems())
                    obj.setMaxVisibleItems(2147483647)
                view = obj.view()
                if obj.property("lunaticOriginalScrollBarPolicy") is None:
                    obj.setProperty("lunaticOriginalScrollBarPolicy", view.verticalScrollBarPolicy().value)
                # Qt's menu-style popup may have disabled this when the combo
                # was created in Current; Neo uses a scrolling list instead.
                view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            elif original is not None:
                obj.setMaxVisibleItems(original)
                obj.setProperty("lunaticOriginalMaxVisibleItems", None)
                obj.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy(obj.property("lunaticOriginalScrollBarPolicy")))
                obj.setProperty("lunaticOriginalScrollBarPolicy", None)
        return False


def polish_tree(root):
    """Maya: apply WidgetAppearancePolicy to one widget tree.

    MoonLab installs that policy as an application-wide event filter. Inside
    Maya that would restyle Maya's own widgets and run Python for every event in
    the application, so the editor calls this on its own widgets instead (after
    building the window and each Parameters rebuild).
    """
    policy = WidgetAppearancePolicy(root)
    for widget in [root, *root.findChildren(QWidget)]:
        policy.eventFilter(widget, QEvent(QEvent.Type.Polish))
    policy.deleteLater()


def stylesheet_path(path):
    return Path(os.path.expandvars(str(path))).expanduser().resolve()


def read_stylesheet(path):
    """Resolve relative image URLs without changing the process working directory."""
    path = stylesheet_path(path)
    source = path.read_text(encoding="utf-8-sig")
    source = QSS_TOKENS.sub(lambda match: " " if match[0].startswith("/*") else match[0], source)

    def resource(match):
        url = next(value for value in match.groups() if value is not None).strip()
        if not url or url.startswith((":", "/", "#")) or re.match(r"^[a-zA-Z][\w+.-]*:", url):
            return match[0]
        resolved = (path.parent / url).resolve().as_posix()
        return 'url("' + resolved.replace("\\", "\\\\").replace('"', '\\"') + '")'

    return QSS_URL.sub(resource, source)


def stylesheet(default, theme="default", path=None):
    if theme == "default":
        return default
    if theme == "neo":
        folder = THEME_ROOT / "neo"
        return FOUNDATION + "\n" + read_stylesheet(folder / "neo_style.css") + "\n" + read_stylesheet(folder / "lunatic.qss")
    if theme == "usd-explorer":
        return read_stylesheet(THEME_ROOT / "usd-explorer" / "usd_explorer.qss").strip()
    if theme == "custom" and path:
        return FOUNDATION + "\n" + read_stylesheet(path)
    raise ValueError("Choose Current, Neo, USD Explorer, or a custom stylesheet file.")


def color_palette(base, data):
    """Apply the captured active/inactive and disabled colors to a base palette."""
    palette = QPalette(base)
    for group, colors in ((QPalette.ColorGroup.All, data["colors"]),
                          (QPalette.ColorGroup.Disabled, data["disabled"])):
        for name, color in colors.items():
            palette.setColor(group, getattr(QPalette.ColorRole, name), QColor(color))
    return palette


def alternate_theme():
    app = QApplication.instance()
    return bool(app and app.property("lunaticAlternateTheme"))


def local_stylesheet(widget, styles):
    if QApplication.instance().property("lunaticExplorerTheme"):
        if isinstance(widget, QGroupBox):
            return ""
        return styles[2] if styles[2] is not None else styles[1]
    return styles[bool(alternate_theme())]


def set_local_style(widget, default, alternate="", *, explorer=None):
    """Retain layout/semantic styles while allowing application colors to change."""
    LOCAL_STYLES[widget] = (default, alternate, explorer)
    widget.setStyleSheet(local_stylesheet(widget, LOCAL_STYLES[widget]))


def style_loaded_form(root):
    # The Designer form embeds the original theme for standalone previews.
    set_local_style(root, root.styleSheet())
    for box in root.findChildren(QGroupBox):
        set_local_style(box, box.styleSheet(), GROUP_LAYOUT)


def menu_appearance():
    """Portable menu style for the viewport's separate Qt5 process."""
    app = QApplication.instance()
    palette, font = app.palette("QMenu"), app.font("QMenu")
    roles = ("Window", "WindowText", "Base", "AlternateBase", "Text", "Button", "ButtonText",
             "Highlight", "HighlightedText", "Light", "Midlight", "Mid", "Dark", "Shadow",
             "BrightText", "ToolTipBase", "ToolTipText", "Link", "LinkVisited", "PlaceholderText")
    return dict(stylesheet=app.styleSheet(), palette={group: {
        role: palette.color(getattr(QPalette.ColorGroup, group), getattr(QPalette.ColorRole, role)).name(QColor.NameFormat.HexArgb)
        for role in roles} for group in ("Active", "Inactive", "Disabled")},
        font=dict(family=font.family(), point_size=font.pointSizeF(), pixel_size=font.pixelSize(),
                  bold=font.bold(), italic=font.italic()))


class Appearance(QObject):
    changed = Signal()

    def __init__(self, default):
        super().__init__()
        self.default = default
        self.theme = "default"
        self.path = None
        self.restart_arguments = []
        self.actions = {}
        self.store = recent_scenes.settings_store()
        app = QApplication.instance()
        if not hasattr(app, "_lunatic_widget_appearance_policy"):
            app._lunatic_widget_appearance_policy = WidgetAppearancePolicy(app)
            app.installEventFilter(app._lunatic_widget_appearance_policy)
        self.original_palette = QPalette(app.palette())
        self.original_font = QFont(app.font())
        self.original_widget_palettes = {name: QPalette(app.palette(name)) for name in EXPLORER_PALETTE_CLASSES}

    def apply(self, theme, path=None, *, persist=False):
        path = stylesheet_path(path) if theme == "custom" and path else None
        # Read first: a missing/unreadable file must leave the existing theme intact.
        source = stylesheet(self.default, theme, path)
        palette, font = self.original_palette, self.original_font
        widget_palettes = self.original_widget_palettes
        if theme == "usd-explorer":
            data = json.loads((THEME_ROOT / "usd-explorer" / "palette.json").read_text(encoding="utf-8"))
            palette = color_palette(self.original_palette, data)
            font = QFont(data["font"]["family"], data["font"]["point_size"])
            widget_palettes = {name: color_palette(palette, colors) for name, colors in data["widgets"].items()}
        app = QApplication.instance()
        app.setProperty("lunaticNeoTheme", theme == "neo")
        app.setProperty("lunaticExplorerTheme", theme == "usd-explorer")
        app.setProperty("lunaticAlternateTheme", theme != "default")
        if theme == "usd-explorer":
            # QStyleSheetStyle changes control geometry even for font-only rules.
            app.setStyleSheet("")
        app.setPalette(palette)
        for name, widget_palette in widget_palettes.items():
            app.setPalette(widget_palette, name)
        app.setFont(font)
        app.setStyleSheet(source)
        # Visit only registered overrides. allWidgets() can create temporary
        # Python wrappers for Qt-owned menus and interfere with their lifetime.
        for widget, styles in list(LOCAL_STYLES.items()):
            if isValid(widget):
                widget.setStyleSheet(local_stylesheet(widget, styles))
        self.theme, self.path = theme, path
        self.restart_arguments = ["--stylesheet", str(path)] if path else ["--theme", theme]
        if persist:
            self.store.setValue(THEME_KEY, theme)
            if path:
                self.store.setValue(PATH_KEY, str(path))
            self.store.sync()
        self.update_actions()
        self.changed.emit()

    def launch(self, theme=None, path=None):
        """CLI overrides preferences for this run; unavailable saved files fall back."""
        if path is not None or theme is not None:
            self.apply("custom" if path is not None else theme, path)
            return ""
        theme = self.store.value(THEME_KEY, "default")
        path = self.store.value(PATH_KEY, "") if theme == "custom" else None
        try:
            self.apply(theme, path)
        except (OSError, UnicodeError, ValueError) as exc:
            self.apply("default")
            return f"Could not load saved appearance; using Current: {exc}"
        return ""

    def add_menu(self, window):
        self.window = window
        menu = self.menu = window.menuBar().addMenu("Appearance")
        menu.setToolTipsVisible(True)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for key, title in (("default", "Current"), ("neo", "Neo"), ("usd-explorer", "USD Explorer"),
                           ("custom", "Custom stylesheet…")):
            action = self.actions[key] = menu.addAction(title)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, key=key: self.choose(key))
        self.actions["default"].setToolTip("The original Lunatic stylesheet")
        self.actions["neo"].setToolTip("Gray theme adapted from Prism's neo_style.css")
        self.actions["usd-explorer"].setToolTip("USD Explorer's native dark Fusion controls, Ubuntu Sans, and orange selection")
        self.update_actions()

    def update_actions(self):
        for key, action in self.actions.items():
            action.setChecked(key == self.theme)
        if "custom" in self.actions:
            self.actions["custom"].setToolTip(str(self.path or "Load a Qt stylesheet (.qss or .css)"))

    def choose(self, theme):
        path = None
        if theme == "custom":
            path, _ = QFileDialog.getOpenFileName(self.window, "Choose stylesheet",
                str(self.path or self.store.value(PATH_KEY, "")), "Qt stylesheets (*.qss *.css);;All files (*)")
            if not path:
                self.update_actions()
                return
        try:
            self.apply(theme, path, persist=True)
        except (OSError, UnicodeError, ValueError) as exc:
            self.update_actions()
            QMessageBox.warning(self.window, "Cannot load stylesheet", str(exc))
            return
        title = {"default": "Current", "neo": "Neo", "usd-explorer": "USD Explorer", "custom": "Custom"}[theme]
        self.window.statusBar().showMessage(f"{title} appearance applied and saved for the next launch.", 5000)
