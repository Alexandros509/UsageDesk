"""Render real widgets with synthetic data, without reading the user's app data."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from usagedesk.bar import DisplayDialog  # noqa: E402
from usagedesk.domain import QuotaLimit  # noqa: E402
from usagedesk.launcher import LauncherEntry  # noqa: E402
from usagedesk.providers import Snapshot  # noqa: E402
from usagedesk.storage import ConfigStore  # noqa: E402
from usagedesk.ui import MainWindow  # noqa: E402

root = Path(__file__).resolve().parents[1]
output = root / "docs" / "images"
output.mkdir(parents=True, exist_ok=True)
data = (root / ".test-data" / ("readme-" + uuid4().hex)).resolve()
assert data.is_relative_to((root / ".test-data").resolve())
data.mkdir(parents=True)
app = QApplication([])
font = Path(os.environ["WINDIR"]) / "Fonts" / "malgun.ttf"
if font.is_file():
    QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Malgun Gothic", 10))
window = MainWindow(ConfigStore(data), tray_enabled=False)
window.timer.stop()
window.usage.timer.stop()
window.usage.pool.waitForDone()
app.processEvents()
window.entries = [
    LauncherEntry("Project Dashboard", r"C:\UsageDeskDemo\dashboard.html", r"C:\UsageDeskDemo"),
    LauncherEntry("Python 작업 도구", r"C:\UsageDeskDemo\tool.py", r"C:\UsageDeskDemo"),
    LauncherEntry("문서와 자료 모음", r"C:\UsageDeskDemo\guide.pdf", r"C:\UsageDeskDemo"),
]
window.render()
now = datetime.now(UTC)
for provider, values in (("claude", (42, 68)), ("codex", (18, 54))):
    window.usage.snapshots[provider] = Snapshot(now, [
        QuotaLimit("five_hour", "5시간", values[0], now + timedelta(hours=3, minutes=21), 18000),
        QuotaLimit("seven_day", "7일", values[1], now + timedelta(days=2, hours=4), 604800),
    ])
window.usage.snapshots["grok"] = Snapshot(now, [
    QuotaLimit("account", "통합 주간 한도", 35, now + timedelta(hours=23, minutes=45), 604800),
])
window.usage.render()
for card in window.usage.cards.values():
    card[0].setText("예시 데이터 · 실제 계정과 무관")
window.bar.options = replace(window.bar.options, appearance="dark", reset_display="inline",
                             quota_display="remaining", size="standard",
                             programs=tuple(entry.id for entry in window.entries))
window.bar.available_geometry = lambda: QRect(0, 0, 1920, 1080)
window.bar.refresh()
window.bar.show()
app.processEvents()
window.bar.grab().save(str(output / "toolbar-dark.png"))
window.bar.options = replace(window.bar.options, appearance="light")
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / "toolbar-light.png"))
window.bar.options = replace(window.bar.options, appearance="dark")
window.bar.refresh()
settings = DisplayDialog(window.bar, window.bar.options, window.usage.snapshots, window.entries)
settings.resize(490, 850)
settings.show()
app.processEvents()
settings.grab().save(str(output / "display-settings.png"))
settings.hide()
window.tabs.setCurrentIndex(1)
window.show()
app.processEvents()
window.grab().save(str(output / "file-launcher.png"))
window.tabs.setCurrentIndex(2)
app.processEvents()
window.grab().save(str(output / "hotkey-settings.png"))
window.entries += [LauncherEntry(f"Project Workspace {i}", rf"C:\UsageDeskDemo\{i}.html",
                                r"C:\UsageDeskDemo") for i in range(8)]
window.bar.options = replace(window.bar.options, programs=tuple(e.id for e in window.entries))
window.bar.available_geometry = lambda: QRect(0, 0, 1100, 1080)
window.bar.relayout()
app.processEvents()
window.bar.grab().save(str(output / "toolbar-overflow.png"))
window.quitting = True
window.usage.shutdown()
window.usage.pool.waitForDone()
window.bar.hide()
window.hide()
print("Rendered 6 README screenshots with isolated synthetic data.")
