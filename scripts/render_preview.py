"""Render real Qt widgets for layout QA without opening desktop windows."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from usagedesk.bar import DisplayDialog  # noqa: E402
from usagedesk.domain import QuotaLimit  # noqa: E402
from usagedesk.launcher import LauncherEntry  # noqa: E402
from usagedesk.providers import Snapshot  # noqa: E402
from usagedesk.storage import ConfigStore  # noqa: E402
from usagedesk.ui import EntryDialog, MainWindow  # noqa: E402

app = QApplication([])
# The offscreen backend has no Windows system-font discovery. Load an installed
# OS font for QA only; it is neither copied nor distributed with the application.
font = Path(os.environ["WINDIR"]) / "Fonts" / "malgun.ttf"
if font.is_file():
    QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Malgun Gothic", 10))

output = Path(".test-data")
output.mkdir(exist_ok=True)
window = MainWindow(ConfigStore(output / "render-only"), tray_enabled=False)
window.entries = [
    LauncherEntry("Python 작업", r"C:\개발 프로젝트\run.cmd", r"C:\개발 프로젝트"),
    LauncherEntry("로그 보기", r"C:\개발 프로젝트\logs.cmd", r"C:\개발 프로젝트"),
]
window.render()
window.tabs.setCurrentIndex(1)
window.show()
app.processEvents()
window.grab().save(str(output / "preview-programs.png"))
window.tabs.setCurrentIndex(0)
app.processEvents()
window.grab().save(str(output / "preview-usage.png"))
window.bar.show_bar()
app.processEvents()
window.bar.grab().save(str(output / "preview-bar-disconnected.png"))
# Synthetic display fixtures only; never stored or used for the actual account view.
for key, values in (("claude", (42, 68)), ("codex", (18, 54))):
    window.usage.snapshots[key] = Snapshot(datetime.now(UTC), [
        QuotaLimit("five_hour", "5시간", values[0], window_seconds=18000),
        QuotaLimit("seven_day", "7일", values[1], window_seconds=604800),
    ])
    window.usage.cards[key][0].setText("미리보기용 예시 데이터")
window.usage.snapshots["grok"] = Snapshot(datetime.now(UTC), [
    QuotaLimit("account", "통합 주간 한도", 35, window_seconds=604800),
])
window.usage.cards["grok"][0].setText("미리보기용 예시 데이터")
window.bar.options = replace(window.bar.options, appearance="dark",
                             programs=tuple(e.id for e in window.entries))
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / "preview-bar-example-dark.png"))
window.bar.options = replace(window.bar.options, appearance="light")
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / "preview-bar-example-light.png"))

# Wide-monitor example of the selected two-line countdown layout.
window.bar.available_geometry = lambda: QRect(0, 0, 1920, 1080)
for snapshot in window.usage.snapshots.values():
    snapshot.limits = [replace(q, reset_at_utc=datetime.now(UTC) + timedelta(
        seconds=12000 if q.window_seconds == 18000 else 100000)) for q in snapshot.limits]
window.bar.options = replace(window.bar.options, reset_display="inline", appearance="dark")
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / "preview-bar-countdown-used.png"))
window.bar.options = replace(window.bar.options, quota_display="remaining")
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / "preview-bar-countdown-remaining.png"))
# Reproduce a depleted weekly account with an idle five-hour window.
claude_preview = window.usage.snapshots["claude"]
original_limits = claude_preview.limits
claude_preview.limits = [replace(q, used_percent=0 if q.key == "five_hour" else 100,
                                reset_at_utc=None if q.key == "five_hour" else q.reset_at_utc)
                         for q in original_limits]
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / "preview-bar-weekly-exhausted.png"))
claude_preview.extra_usage_enabled = True
window.bar.refresh()
app.processEvents()
window.bar.grab().save(str(output / "preview-bar-weekly-extra.png"))
claude_preview.limits = original_limits
claude_preview.extra_usage_enabled = False
window.bar.refresh()

display = DisplayDialog(window.bar, window.bar.options, window.usage.snapshots, window.entries)
display.show()
app.processEvents()
display.grab().save(str(output / "preview-display-settings.png"))
display.tabs.setCurrentIndex(1)
app.processEvents()
display.grab().save(str(output / "preview-program-settings.png"))
display.hide()
window.bar.options = replace(window.bar.options, placement="top", appearance="dark")
window.bar.refresh()
window.bar.show_bar()
app.processEvents()
window.bar.grab().save(str(output / "preview-top-bar.png"))
dialog = EntryDialog(window)
dialog.show()
app.processEvents()
dialog.grab().save(str(output / "preview-dialog.png"))
# Full program names in two lines, including overflow on a narrow display.
window.entries = [LauncherEntry(name, rf"C:\Apps\{index}.html", r"C:\Apps")
                  for index, name in enumerate([
                      "TIL MemoRE:Flow", "TIL MultiPlatform", "HTML 작업 대시보드",
                      "프로젝트별 작업 기록", "문서와 자료 모음", "Development Workbench",
                      "회의 기록과 메모", "사용량 분석 리포트",
                  ])]
window.bar.options = replace(window.bar.options, programs=tuple(e.id for e in window.entries))
for width in (1920, 800):
    window.bar.available_geometry = lambda width=width: QRect(0, 0, width, 1080)
    window.bar.relayout()
    app.processEvents()
    window.bar.grab().save(str(output / f"preview-program-names-{width}.png"))
window.timer.stop()
window.usage.shutdown()
window.usage.pool.waitForDone()
