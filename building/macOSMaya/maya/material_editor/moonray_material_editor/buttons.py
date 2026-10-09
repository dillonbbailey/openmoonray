"""Button labels that keep a little breathing room in compact controls."""
import math

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontInfo, QFontMetrics
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton, QStylePainter


class FittingPushButton(QPushButton):
    extra_width = 0

    def sizeHint(self):
        return super().sizeHint() + QSize(self.extra_width, 0)

    def label_font(self, option):
        font = self.font()
        # Respect theme padding, with at least 4px beside and 2px above/below
        # the label on controls whose compact style removes that padding.
        area = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        area = area.intersected(self.rect().adjusted(4, 2, -4, -2))
        width = area.width()
        if not option.icon.isNull():
            width -= option.iconSize.width() + 4
        if option.features & QStyleOptionButton.ButtonFeature.HasMenu:
            width -= self.style().pixelMetric(QStyle.PixelMetric.PM_MenuButtonIndicator, option, self)
        flags = Qt.TextFlag.TextShowMnemonic | Qt.TextFlag.TextSingleLine

        def fits(candidate):
            size = QFontMetrics(candidate, self).size(flags, option.text)
            return size.width() <= width and size.height() <= area.height()

        if not option.text or fits(font):
            return font
        # Fit at paint time: retain the theme font and size hint so resizing
        # and live theme switches never accumulate smaller font sizes.
        pixels = QFontInfo(font).pixelSize()
        minimum = min(pixels, max(9, math.floor(pixels * .8)))
        fitted = QFont(font)
        for size in range(pixels - 1, minimum - 1, -1):
            fitted.setPixelSize(size)
            if fits(fitted):
                break
        return fitted

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        font = self.label_font(option)
        if font == self.font():
            return super().paintEvent(event)
        painter = QStylePainter(self)
        painter.setFont(font)
        option.fontMetrics = QFontMetrics(font, self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
