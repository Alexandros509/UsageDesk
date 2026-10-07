"""Bounded Windows AppBar integration check with work-area restoration in finally."""

import os
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

from usagedesk.bar import UsageBar  # noqa: E402
from usagedesk.usage_ui import UsagePane  # noqa: E402

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
owner = QWidget()
owner.entries = []
owner.quitting = False
owner.tray_menu = QMenu(owner)
pane = UsagePane(Path(".test-data") / f"dock-{uuid4().hex}", autoload=False)
bar = UsageBar(owner, pane, pane.sessions["claude"].vault.directory.parent)
before = app.primaryScreen().availableGeometry()


def pump():
    deadline = time.monotonic() + .4
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)


try:
    bar.options = replace(bar.options, placement="top")
    bar.refresh()
    bar.show_bar()
    pump()
    after = app.primaryScreen().availableGeometry()
    assert bar.dock.registered, "AppBar registration failed"
    assert after.top() >= before.top() + bar.height() - 1, (before, after, bar.geometry())
    assert bar.geometry().bottom() < after.top(), (bar.geometry(), after)
    print("AppBar registered; maximized-window work area reserved.")
    bar.options = replace(bar.options, size="prominent")
    bar.refresh()
    pump()
    resized = app.primaryScreen().availableGeometry()
    assert resized.top() >= before.top() + bar.height() - 1
    assert bar.geometry().bottom() < resized.top()
    print("AppBar height change updated reserved work area.")
finally:
    bar.dock.remove()
    bar.hide()
    pane.shutdown()
    pane.pool.waitForDone()
    pump()
    restored = app.primaryScreen().availableGeometry()
    assert restored == before, (before, restored)
    print("AppBar removed; original work area restored.")
