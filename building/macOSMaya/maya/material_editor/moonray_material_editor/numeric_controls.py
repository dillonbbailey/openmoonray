"""Compact numeric displays that preserve full-precision values."""
import math

from PySide6.QtCore import QEvent, QLocale, QSignalBlocker, Qt
from PySide6.QtGui import QDoubleValidator, QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QPlainTextEdit

from .application_settings import MAX_DECIMALS, preferences
from .number_format import DISPLAY_DECIMALS, compact_json, compact_number, value_decimals


class NumericSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None, *, numeric_type="float"):
        self.numeric_type = numeric_type
        self._display_decimals = DISPLAY_DECIMALS
        self._extra_decimals = 0
        self._precision_wheel_delta = 0
        self._precision_wheel_pixels = 0
        self._editing = False
        super().__init__(parent)
        self.lineEdit().installEventFilter(self)
        self.valueChanged.connect(self.reset_extra_precision)
        self.setAccessibleDescription(
            "Place the caret after the last fractional digit and scroll up to reveal more decimal places "
            "without changing the value. Scroll down to hide unused extra places. Leaving the field hides unused "
            "extra places while keeping meaningful digits.")
        self.preferences = preferences()
        self.preferences.changed.connect(self.apply_precision)
        self.apply_precision()

    def set_numeric_type(self, numeric_type):
        if numeric_type not in ("float", "double"):
            raise ValueError("Numeric type must be float or double.")
        self.numeric_type = numeric_type
        self.apply_precision()

    def apply_precision(self):
        self.setDecimals(self.preferences.precisions[self.numeric_type])

    def decimals(self):
        return value_decimals(self.value(), self._display_decimals) + self._extra_decimals

    def reset_extra_precision(self, *_):
        extra = self._extra_decimals
        self._extra_decimals = 0
        self._precision_wheel_delta = self._precision_wheel_pixels = 0
        if extra and not self.lineEdit().isModified():
            self.refresh_text()

    def setValue(self, value):
        if value != self.value():
            # Also handle programmatic updates made under a QSignalBlocker.
            self.reset_extra_precision()
        super().setValue(value)

    def setDecimals(self, places):
        self._display_decimals = max(0, min(323, int(places)))
        self._extra_decimals = 0
        self._precision_wheel_delta = self._precision_wheel_pixels = 0
        self.setProperty("numericType", self.numeric_type)
        with QSignalBlocker(self):
            # Qt's native decimal limit also rounds value(), including setValue
            # calls. Keep its full range and customize only the displayed text.
            if super().decimals() != 323:
                super().setDecimals(323)
            field = self.lineEdit()
            if not field.isModified():
                self.refresh_text()
        self.updateGeometry()

    def refresh_text(self):
        text = self.specialValueText() if self.value() == self.minimum() and self.specialValueText() else (
            self.prefix() + self.textFromValue(self.value()) + self.suffix())
        with QSignalBlocker(self.lineEdit()):
            self.lineEdit().setText(text)

    def textFromValue(self, value):
        locale = QLocale(self.locale())
        if not self.isGroupSeparatorShown():
            locale.setNumberOptions(locale.numberOptions() | QLocale.NumberOption.OmitGroupSeparator)
        places = value_decimals(value, self._display_decimals) + self._extra_decimals
        style = "e" if "e" in compact_number(value, places) else "f"
        return locale.toString(value, style, places)

    def valueFromText(self, text):
        displayed = self.prefix() + self.textFromValue(self.value()) + self.suffix()
        # interpretText/focus changes may parse the formatted text even when the
        # user made no edit. In that case keep the original full-precision value.
        if not self.lineEdit().isModified() and text == displayed:
            return self.value()
        value, valid = self.locale().toDouble(self.number_text(text))
        if valid and math.isfinite(value):
            return value
        return super().valueFromText(text)

    def number_text(self, text):
        if self.prefix() and text.startswith(self.prefix()):
            text = text[len(self.prefix()):]
        if self.suffix() and text.endswith(self.suffix()):
            text = text[:-len(self.suffix())]
        return text.strip()

    def validate(self, text, position):
        if self.specialValueText() and text == self.specialValueText():
            return QValidator.State.Acceptable, text, position
        validator = QDoubleValidator(self.minimum(), self.maximum(), 323)
        validator.setLocale(self.locale())
        validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
        value = self.number_text(text)
        state, _, _ = validator.validate(value, len(value))
        return state, text, position

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._editing = True
        self.refresh_text()
        self.selectAll()

    def focusOutEvent(self, event):
        # Commit the entered text before hiding unused extra decimal places.
        super().focusOutEvent(event)
        self._editing = False
        self._extra_decimals = 0
        self._precision_wheel_delta = self._precision_wheel_pixels = 0
        self.refresh_text()

    def fractional_end(self):
        """Find the end of the mantissa, excluding prefixes, suffixes and exponents."""
        text = self.lineEdit().text()
        start = len(self.prefix()) if text.startswith(self.prefix()) else 0
        end = len(text) - len(self.suffix()) if self.suffix() and text.endswith(self.suffix()) else len(text)
        number = text[start:end]
        for index, character in enumerate(number):
            if character in "eE":
                number = number[:index]
                break
        point = number.rfind(self.locale().decimalPoint())
        if point < 0 or not number[point + 1:].isdecimal():
            return None
        return start + len(number), len(number) - point - 1

    def precision_wheel(self, event):
        field = self.lineEdit()
        end = self.fractional_end()
        if (not self._editing or not self.isEnabled() or self.isReadOnly()
                or event.modifiers() != Qt.KeyboardModifier.NoModifier or field.hasSelectedText()
                or end is None or field.cursorPosition() != end[0] or end[1] < self._display_decimals):
            self._precision_wheel_delta = self._precision_wheel_pixels = 0
            return False
        event.accept()
        # Formatting must never commit, round, or discard an unfinished edit.
        if field.isModified():
            return True
        if event.angleDelta().y():
            delta, step, attribute = event.angleDelta().y(), 120, "_precision_wheel_delta"
        else:
            delta, step, attribute = event.pixelDelta().y(), 20, "_precision_wheel_pixels"
        remainder = getattr(self, attribute)
        if remainder * delta < 0:
            remainder = 0
        total = remainder + delta
        steps = math.trunc(total / step)
        setattr(self, attribute, total - steps * step)
        base = value_decimals(self.value(), self._display_decimals)
        extra = max(0, min(max(0, MAX_DECIMALS - base), self._extra_decimals + steps))
        if extra != self._extra_decimals:
            self._extra_decimals = extra
            self.refresh_text()
            field.setCursorPosition(self.fractional_end()[0])
            self.updateGeometry()
        return True

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.Type.Wheel and self.precision_wheel(event):
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event):
        if not self.precision_wheel(event):
            super().wheelEvent(event)


class NumericLineEdit(QLineEdit):
    """Keep the native edit buffer exact; overlay a compact, unfocused display.

    text() and validation always use the source buffer. The display overlay is
    transparent to input, so focus, copy, Apply and text Undo use native editing.
    """
    def __init__(self, text="", parent=None, *, numeric_type="double", json_value=False):
        super().__init__(str(text), parent)
        self.numeric_type = numeric_type
        self.json_value = json_value
        self._display = QLineEdit(self)
        self._display.setObjectName("numeric_display_overlay")
        self._display.setReadOnly(True)
        self._display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._display.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.preferences = preferences()
        self.preferences.changed.connect(self.refresh_display)
        self.textChanged.connect(self.refresh_display)
        self.refresh_display()

    def compact_text(self):
        text = self.text()
        places = self.preferences.precisions[self.numeric_type]
        if self.json_value:
            return compact_json(text, places, preserve_precision=True)
        try:
            value = float(text)
            return compact_number(value, value_decimals(value, places)) if math.isfinite(value) else text
        except ValueError:
            return text

    def displayText(self):
        return super().displayText() if self.hasFocus() else self._display.displayText()

    def refresh_display(self, *_):
        self._display.setText(self.compact_text())
        self._display.setAlignment(self.alignment())
        self._display.setPlaceholderText(self.placeholderText())
        self._display.setGeometry(self.rect())
        self._display.setVisible(not self.hasFocus())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_display"):
            self._display.setGeometry(self.rect())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_display()

    def focusInEvent(self, event):
        self._display.hide()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.refresh_display()


class NumericPlainTextEdit(QPlainTextEdit):
    """JSON editor whose compact overlay leaves its document and Undo intact."""
    def __init__(self, text="", parent=None):
        super().__init__(str(text), parent)
        self._display = QPlainTextEdit(self)
        self._display.setObjectName("numeric_display_overlay")
        self._display.setReadOnly(True)
        self._display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._display.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.preferences = preferences()
        self.preferences.changed.connect(self.refresh_display)
        self.textChanged.connect(self.refresh_display)
        self.verticalScrollBar().valueChanged.connect(self._display.verticalScrollBar().setValue)
        self.refresh_display()

    def displayText(self):
        return self.toPlainText() if self.hasFocus() else self._display.toPlainText()

    def refresh_display(self):
        self._display.setPlainText(compact_json(self.toPlainText(), self.preferences.precisions["double"], preserve_precision=True))
        self._display.setGeometry(self.rect())
        self._display.verticalScrollBar().setValue(self.verticalScrollBar().value())
        self._display.setVisible(not self.hasFocus())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_display"):
            self._display.setGeometry(self.rect())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_display()

    def focusInEvent(self, event):
        self._display.hide()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.refresh_display()
