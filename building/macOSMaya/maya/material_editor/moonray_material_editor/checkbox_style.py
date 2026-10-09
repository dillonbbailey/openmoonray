"""Consistent checkbox outlines and state marks, independent of the Qt theme."""
from . import ui_icons_rc  # Register embedded SVGs, including in loaded Designer forms.


CHECKBOX_STYLE = """
QCheckBox { spacing: 6px; }
QCheckBox:disabled { color: #647988; }
QCheckBox::indicator, QTreeWidget::indicator {
    width: 13px; height: 13px; border: 1px solid #8299aa;
    border-radius: 2px; background: #131e27;
}
QCheckBox::indicator:unchecked, QTreeWidget::indicator:unchecked { image: none; }
QCheckBox::indicator:checked, QTreeWidget::indicator:checked {
    background: #7fcbb5; border-color: #7fcbb5;
    image: url(:/lunatic/checkbox-checked.svg);
}
QCheckBox::indicator:hover, QCheckBox::indicator:focus { border-color: #b9f0df; }
QCheckBox::indicator:disabled, QTreeWidget::indicator:disabled {
    background: #26323b; border-color: #52697a;
}
QCheckBox::indicator:checked:disabled, QTreeWidget::indicator:checked:disabled {
    image: url(:/lunatic/checkbox-checked-disabled.svg);
}
"""
