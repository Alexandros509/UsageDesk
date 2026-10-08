"""Render real widgets with synthetic data, without reading the user's app data."""

import argparse
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

root = Path(__file__).resolve().parents[1]
output = root / "docs" / "images"
output.mkdir(parents=True, exist_ok=True)
data = (root / ".test-data" / ("readme-" + uuid4().hex)).resolve()
assert data.is_relative_to((root / ".test-data").resolve())
data.mkdir(parents=True)
parser = argparse.ArgumentParser()
parser.add_argument('--language', choices=('ko', 'en'), default='ko')
args = parser.parse_args()
from usagedesk.i18n import initialize, save_language  # noqa: E402

save_language(data, args.language, "light")
app = QApplication([])
initialize(data, app)
from usagedesk.domain import QuotaLimit  # noqa: E402
from usagedesk.launcher import LauncherEntry  # noqa: E402
from usagedesk.providers import Snapshot  # noqa: E402
from usagedesk.storage import ConfigStore  # noqa: E402
from usagedesk.ui import MainWindow  # noqa: E402

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
    LauncherEntry(("Python tools" if args.language == "en" else "Python 작업 도구"), r"C:\UsageDeskDemo\tool.py", r"C:\UsageDeskDemo"),
    LauncherEntry(("Documents and resources" if args.language == "en" else "문서와 자료 모음"), r"C:\UsageDeskDemo\guide.pdf", r"C:\UsageDeskDemo"),
]
window.render()
now = datetime.now(UTC)
for provider, values in (("claude", (42, 68)), ("codex", (18, 54))):
    window.usage.snapshots[provider] = Snapshot(now, [
        QuotaLimit("five_hour", ("5 hours" if args.language == "en" else "5시간"), values[0], now + timedelta(hours=3, minutes=21), 18000),
        QuotaLimit("seven_day", ("7 days" if args.language == "en" else "7일"), values[1], now + timedelta(days=2, hours=4), 604800),
    ])
window.usage.snapshots["grok"] = Snapshot(now, [
    QuotaLimit("account", ("Combined weekly limit" if args.language == "en" else "통합 주간 한도"), 35, now + timedelta(hours=23, minutes=45), 604800),
])
window.usage.render()
for card in window.usage.cards.values():
    card[0].setText("Example data · No real account" if args.language == "en" else "예시 데이터 · 실제 계정과 무관")
window.bar.options = replace(window.bar.options, appearance="dark", reset_display="inline",
                             quota_display="remaining", size="standard",
                             programs=tuple(entry.id for entry in window.entries))
window.bar.available_geometry = lambda: QRect(0, 0, 1920, 1080)
window.bar.refresh()
window.bar.show()
app.processEvents()
window.bar.grab().save(str(output / ("toolbar-dark" + ("-en" if args.language == "en" else "") + ".png")))
window.bar.options = replace(window.bar.options, appearance="light")
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / ("toolbar-light" + ("-en" if args.language == "en" else "") + ".png")))
window.bar.options = replace(window.bar.options, appearance="dark")
window.bar.refresh()
window.resize(850, 930)
window.open_settings(4)
app.processEvents()
window.grab().save(str(output / ("display-settings" + ("-en" if args.language == "en" else "") + ".png")))
from usagedesk.appearance import apply_theme  # noqa: E402

apply_theme(app, "dark")
window.resize(850, 600)
window.language_page.appearance.setCurrentIndex(window.language_page.appearance.findData("dark"))
window.tabs.setCurrentIndex(3)
app.processEvents()
window.grab().save(str(output / ("settings-dark" + ("-en" if args.language == "en" else "") + ".png")))
apply_theme(app, "light")
window.resize(850, 600)
window.tabs.setCurrentIndex(1)
window.show()
app.processEvents()
window.grab().save(str(output / ("file-launcher" + ("-en" if args.language == "en" else "") + ".png")))
window.tabs.setCurrentIndex(2)
app.processEvents()
window.grab().save(str(output / ("hotkey-settings" + ("-en" if args.language == "en" else "") + ".png")))
window.entries += [LauncherEntry(f"Project Workspace {i}", rf"C:\UsageDeskDemo\{i}.html",
                                r"C:\UsageDeskDemo") for i in range(8)]
window.bar.options = replace(window.bar.options, programs=tuple(e.id for e in window.entries))
window.bar.available_geometry = lambda: QRect(0, 0, 1100, 1080)
window.bar.relayout()
app.processEvents()
window.bar.grab().save(str(output / ("toolbar-overflow" + ("-en" if args.language == "en" else "") + ".png")))
window.quitting = True
window.usage.shutdown()
window.usage.pool.waitForDone()
window.bar.hide()
window.hide()
print("Rendered 7 README screenshots with isolated synthetic data.")
