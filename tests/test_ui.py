import json
import os
import subprocess
import sys
import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox

from usagedesk.launcher import LauncherEntry
from usagedesk.storage import ConfigStore
from usagedesk.ui import EntryDialog, MainWindow


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def window(app, tmp_path):
    widget = MainWindow(ConfigStore(tmp_path), tray_enabled=False)
    yield widget
    widget.quitting = True
    widget.timer.stop()
    widget.pool.waitForDone(5000)
    app.processEvents()
    widget.hide()
    widget.deleteLater()
    app.processEvents()


def test_program_save_edit_reorder_and_favorite_menu(window):
    first = LauncherEntry("First", r"C:\apps\first.cmd", r"C:\apps")
    second = LauncherEntry("Second", r"C:\apps\second.cmd", r"C:\apps", favorite=True)
    assert window.commit([first, second])
    menu = window.tray_menu.actions()[2].menu()
    assert [a.text() for a in menu.actions()] == ["Second", "First"]
    window.list.setCurrentRow(1)
    window.move(-1)
    assert window.entries == [second, first]
    assert window.store.load() == [second, first]
    assert window.commit([replace(second, name="Changed"), first])
    assert "Changed" in window.list.item(0).text()


def test_save_failure_preserves_ui(window, monkeypatch):
    entry = LauncherEntry("Before", r"C:\apps\run.cmd", r"C:\apps")
    window.commit([entry])

    def denied(_):
        raise PermissionError("simulated write failure")

    monkeypatch.setattr(window.store, "save", denied)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.Ok)
    assert not window.commit([])
    assert window.entries == [entry]
    assert window.list.count() == 1


def test_tray_program_action_dispatches_entry(window, monkeypatch):
    entry = LauncherEntry("Tray", r"C:\apps\run.cmd", r"C:\apps")
    window.commit([entry])
    calls = []
    monkeypatch.setattr(window, "launch", calls.append)
    window.tray_menu.actions()[2].menu().actions()[0].trigger()
    assert calls == [entry]


def test_registration_dialog_never_executes_and_validates_arguments(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "launch", calls.append)
    dialog = EntryDialog(window)
    dialog.name.setText("등록")
    dialog.script.setText(r"C:\apps\run.cmd")
    dialog.cwd.setText(r"C:\apps")
    dialog.args.setPlainText("한글 인수\n\nsecond")
    dialog.save()
    assert calls == []
    assert dialog.result_entry.arguments == ("한글 인수", "second")
    dialog.deleteLater()


def test_html_registration_uses_default_app_and_disables_script_options(window):
    dialog = EntryDialog(window)
    dialog.name.setText("HTML 문서")
    dialog.cwd.setText(r"C:\apps")
    dialog.script.setText(r"C:\apps\index.html")
    assert not dialog.args.isEnabled()
    assert dialog.mode.currentText() == "기본 앱으로 열기"
    assert not dialog.mode.model().item(1).isEnabled()
    assert not dialog.mode.model().item(2).isEnabled()
    assert dialog.value().arguments == ()
    dialog.script.setText(r"C:\apps\run.py")
    assert dialog.args.isEnabled()
    assert dialog.mode.model().item(2).isEnabled()
    dialog.deleteLater()


def test_corrupt_config_blocks_all_mutation_and_launch(app, tmp_path):
    raw = b"broken"
    (tmp_path / "config.json").write_bytes(raw)
    widget = MainWindow(ConfigStore(tmp_path), tray_enabled=False)
    try:
        assert widget.store.blocked
        assert all(not button.isEnabled() for button in widget.edit_buttons)
        widget.launch(LauncherEntry("Blocked", r"C:\apps\run.cmd", r"C:\apps"))
        assert not widget.pending
        assert (tmp_path / "config.json").read_bytes() == raw
    finally:
        widget.timer.stop()
        widget.deleteLater()
        app.processEvents()


def test_worker_launch_runs_off_gui_and_completion_cleans_queue(window, app, monkeypatch):
    entry = LauncherEntry("Worker", r"C:\apps\run.cmd", r"C:\apps")
    threads = []

    def launch(_):
        threads.append(QThread.currentThread())
        return 4321

    monkeypatch.setattr(window.launcher, "start", launch)
    window.launch(entry)
    window.launch(entry)
    deadline = time.monotonic() + 3
    while window.pending and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert len(threads) == 1
    assert threads[0] != app.thread()
    assert not window.pending
    assert not window.launch_pending
    assert "실행 요청 완료" in window.statuses[entry.id]


@pytest.mark.skipif(os.name != "nt", reason="Windows IPC process test")
def test_second_instance_exits_while_first_remains_alive(tmp_path):
    data = tmp_path / "ipc"
    command = [sys.executable, "-m", "usagedesk.app", "--data-dir", str(data)]
    with subprocess.Popen(
        command + ["--smoke-ms", "4500"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as first:
        try:
            deadline = time.monotonic() + 3
            while not (data / "instance.lock").exists() and time.monotonic() < deadline:
                assert first.poll() is None
                time.sleep(0.03)
            second = subprocess.run(
                command + ["--smoke-ms", "20000"],
                timeout=3,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert second.returncode == 0, second.stderr.decode(errors="replace")
            assert first.poll() is None
            _, errors = first.communicate(timeout=8)
            assert first.returncode == 0
            assert b"Traceback" not in errors, errors.decode(errors="replace")
        finally:
            if first.poll() is None:
                first.terminate()
                first.wait(timeout=5)


def test_no_secrets_or_fake_usage_in_initial_store(window):
    window.commit([])
    payload = json.loads(window.store.path.read_text(encoding="utf-8"))
    assert payload == {"schema_version": 1, "programs": []}


def test_closing_detail_returns_to_bar_even_without_system_tray(window, app):
    window.show()
    app.processEvents()
    window.close()
    app.processEvents()
    assert not window.isVisible()
    assert window.bar.isVisible()
    assert not window.quitting


def test_hotkey_toggle_restores_docked_bar_and_settings_suppress_it(window):
    from usagedesk.hotkeys import parse_key
    window.tray_available = True
    window.bar.options = replace(window.bar.options, placement="bottom")
    window.bar.show_bar()
    before = window.bar.pos()
    window.hotkeys.apply({"enabled": True, "keys": {"toggle": "Ctrl+Alt+Shift+F10"}})
    identifier = window.hotkeys.bindings[parse_key("Ctrl+Alt+Shift+F10")][0]
    try:
        window.hotkeys.dispatch(identifier)
        assert not window.bar.isVisible() and window.bar.options.tray_mode
        window.hotkeys.dispatch(identifier)
        assert window.bar.isVisible() and window.bar.options.placement == "bottom"
        assert window.bar.pos() == before
        window.open_tab(2)
        QApplication.instance().processEvents()
        window.hotkeys.dispatch(identifier)
        assert window.bar.isVisible()
    finally:
        window.hotkeys.close()
        window.bar.hide()
        window.bar.dock.remove()


def test_background_settings_does_not_block_global_hotkeys(window, monkeypatch):
    window.tabs.setCurrentIndex(2)
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    assert not window.editing_hotkeys()
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    assert window.editing_hotkeys()
    window.tabs.setCurrentIndex(0)
    assert not window.editing_hotkeys()


def test_settings_entry_points_use_one_window_and_save_display(window, app):
    from PySide6.QtWidgets import QDialog
    window.bar.settings_dialog()
    assert window.tabs.currentWidget() is window.display_page
    assert window.display_editor.isVisible()
    assert not any(isinstance(w, QDialog) and w.isVisible() and w.isWindow()
                   for w in app.topLevelWidgets())
    editor = window.display_editor
    editor.mode.setCurrentIndex(editor.mode.findData('custom'))
    editor.hide_unselected.setChecked(True)
    editor.validate()
    assert window.bar.options.hide_unselected
    assert not [item for item in window.bar.items if item[1] == 'provider']
    assert window.display_editor is editor and editor.isVisible()
    settings = next(a.menu() for a in window.tray_menu.actions() if a.text() == '설정')
    assert [a.text() for a in settings.actions()] == ['바 표시', '프로그램 관리', '단축키 설정', '일반 설정']
    for action, index in zip(settings.actions(), [4, 1, 2, 3], strict=True):
        action.trigger()
        assert window.tabs.currentIndex() == index
    actions = window.tray_menu.actions()
    index = next(i for i,a in enumerate(actions) if a.text() == '설정')
    assert actions[index-1].isSeparator()


@pytest.mark.parametrize('size', [(600, 430), (850, 600), (960, 820)])
def test_display_save_stays_visible_when_content_scrolls(window, app, size):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton
    window.open_settings(4)
    window.resize(*size)
    app.processEvents()
    editor = window.display_editor
    save = editor.findChild(QPushButton, 'saveDisplay')
    before = save.mapTo(window, QPoint(0, 0))
    scroll = editor.display_scroll.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    app.processEvents()
    assert save.mapTo(window, QPoint(0, 0)) == before
    assert save.isVisible()
    assert window.rect().contains(before)
    assert window.rect().contains(save.mapTo(window, save.rect().bottomRight()))
    assert not editor.display_scroll.isAncestorOf(save)
