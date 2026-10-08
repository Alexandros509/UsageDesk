"""Consistent application-window colors, independent of the toolbar appearance."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

DARK_COLORS = {
    '#f5f6f8': '#20242b', '#ffffff': '#292f38', 'white': '#292f38',
    '#303747': '#e7edf5', '#f0f3f8': '#252b33', '#dbeaff': '#344d70',
    '#163d71': '#ffffff', '#78869b': '#a1adbf', '#dfe3ea': '#46505f',
    '#e7ebf2': '#2b323d', '#536177': '#c0cbda', '#173e71': '#edf4ff',
    '#edf3fc': '#344154', '#d9dfe8': '#526074', '#8e9cb1': '#8d9fb7',
    '#e8edf4': '#353d49', '#c7d0df': '#566275', '#778397': '#a1adbf',
    '#cbd4e1': '#526074', '#dce9fb': '#3e4d63', '#143967': '#ffffff',
    '#e1edff': '#344d70', '#edf3ff': '#344154',
}


def dark_windows():
    app = QApplication.instance()
    return bool(app and app.property('usagedesk_dark_windows'))


def theme_color(color):
    return DARK_COLORS.get(color, color) if dark_windows() else color


def themed_style(style):
    if dark_windows():
        # Replace original tokens once; do not transform replacement colors again.
        import re
        style = re.sub(r'#[0-9a-fA-F]{6}|\bwhite\b',
                       lambda match: DARK_COLORS.get(match[0], match[0]), style)
    return style


def apply_theme(app, choice):
    dark = choice == 'dark' or (choice == 'system' and
                               app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    app.setProperty('usagedesk_dark_windows', dark)
    app.setStyle('Fusion')
    palette = QPalette()
    for role, light, night in (
        (QPalette.Window, '#f5f6f8', '#20242b'),
        (QPalette.WindowText, '#303747', '#e7edf5'),
        (QPalette.Base, '#ffffff', '#292f38'),
        (QPalette.AlternateBase, '#f0f3f8', '#252b33'),
        (QPalette.Text, '#303747', '#e7edf5'),
        (QPalette.Button, '#ffffff', '#292f38'),
        (QPalette.ButtonText, '#303747', '#e7edf5'),
        (QPalette.Highlight, '#2563eb', '#356ddd'),
        (QPalette.HighlightedText, '#ffffff', '#ffffff'),
        (QPalette.ToolTipBase, '#ffffff', '#292f38'),
        (QPalette.ToolTipText, '#303747', '#e7edf5'),
        (QPalette.PlaceholderText, '#78869b', '#a1adbf'),
    ):
        palette.setColor(role, QColor(night if dark else light))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor('#a1adbf' if dark else '#778397'))
    app.setPalette(palette)
    # Update explicit popup styles after Qt finishes palette propagation. Replacing
    # a popup from paletteChanged can destroy widgets during Qt's own traversal.
    for window in app.topLevelWidgets():
        for widget in [window, *window.findChildren(QWidget)]:
            refresh = getattr(widget, 'refresh_theme', None)
            if callable(refresh):
                refresh()
