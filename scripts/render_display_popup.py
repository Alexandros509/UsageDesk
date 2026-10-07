"""Render expanded settings lists under a dark system palette (synthetic UI only)."""

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory  # noqa: E402

from usagedesk.bar import DisplayDialog, DisplayOptions  # noqa: E402

app = QApplication([])
font = Path(os.environ["WINDIR"]) / "Fonts" / "malgun.ttf"
if font.is_file():
    QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Malgun Gothic", 10))
palette = QPalette()
for role in (QPalette.Window, QPalette.Base, QPalette.Button):
    palette.setColor(role, QColor("#202020"))
for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.HighlightedText):
    palette.setColor(role, QColor("#ffffff"))
palette.setColor(QPalette.Highlight, QColor("#1675b9"))
app.setPalette(palette)
output = Path(".test-data")
output.mkdir(exist_ok=True)
for style in QStyleFactory.keys():
    app.setStyle(style)
    dialog = DisplayDialog(None, DisplayOptions(), {})
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / f"settings-{style}-dark-system.png"))
    for name in ("theme", "size", "appearance", "mode", "length", "placement"):
        combo = getattr(dialog, name)
        combo.showPopup()
        app.processEvents()
        combo.view().window().grab().save(str(output / f"popup-{style}-{name}.png"))
        print(style, name, [combo.itemText(i) for i in range(combo.count())])
        combo.hidePopup()
    dialog.hide()
    dialog.deleteLater()
    app.processEvents()
