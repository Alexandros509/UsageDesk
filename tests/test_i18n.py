"""Regression checks for persisted languages and translation formatting."""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from string import Formatter

import pytest

from usagedesk import i18n
from usagedesk.i18n_catalog import EN


def test_catalog_preserves_format_fields_and_has_no_korean_values():
    def fields(text):
        return sorted((name, spec, conversion) for _, name, spec, conversion
                      in Formatter().parse(text) if name is not None)
    for source, translated in EN.items():
        assert fields(source) == fields(translated), source
        assert not re.search('[가-힣]', translated), source


def test_all_translation_calls_are_catalogued():
    root = Path(__file__).parents[1] / 'src' / 'usagedesk'
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text('utf-8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr':
                assert isinstance(node.args[0], ast.Constant), path
                assert node.args[0].value in EN, (path, node.args[0].value)


@pytest.mark.parametrize('value', ['en', 'ko', 'bad', None, [], {'language': 'bad'}])
def test_language_persistence_and_invalid_fallback(tmp_path, value):
    try:
        if value in ('en', 'ko'):
            i18n.save_language(tmp_path, value)
        else:
            (tmp_path / 'language.json').write_text(json.dumps(value), 'utf-8')
        assert i18n.initialize(tmp_path) == (value if value in ('en', 'ko') else 'ko')
    finally:
        i18n._language = 'ko'


def test_language_save_failure_does_not_replace_previous_file(tmp_path, monkeypatch):
    i18n.save_language(tmp_path, 'ko')
    previous = (tmp_path / 'language.json').read_bytes()
    monkeypatch.setattr(i18n.QSaveFile, 'commit', lambda self: False)
    with pytest.raises(OSError):
        i18n.save_language(tmp_path, 'en')
    assert (tmp_path / 'language.json').read_bytes() == previous


def test_english_ui_in_fresh_process(tmp_path):
    i18n.save_language(tmp_path, 'en')
    code = r'''
import re, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QLabel, QAbstractButton, QComboBox
from usagedesk.i18n import initialize
app = QApplication([])
data = Path(sys.argv[1])
assert initialize(data, app) == 'en'
from usagedesk.storage import ConfigStore
from usagedesk.ui import MainWindow, EntryDialog
from usagedesk.usage_ui import LoginDialog, GrokLoginDialog
from usagedesk.appearance import apply_theme
from PySide6.QtGui import QPalette
from usagedesk.bar import DisplayDialog
window = MainWindow(ConfigStore(data), tray_enabled=False)
window.timer.stop()
window.usage.timer.stop()
dialog = DisplayDialog(window.bar, window.bar.options, window.usage.snapshots, [])
texts = [window.tabs.tabText(i) for i in range(window.tabs.count())]
assert 'General settings' in texts
extra = [EntryDialog(window), LoginDialog('claude', window), LoginDialog('codex', window), GrokLoginDialog(window)]
for owner in (window, dialog, *extra):
    for widget in owner.findChildren(QLabel) + owner.findChildren(QAbstractButton):
        texts.append(widget.text())
    for combo in owner.findChildren(QComboBox):
        texts.extend(combo.itemText(i) for i in range(combo.count()))
texts.extend(action.text() for action in window.tray_menu.actions())
assert not [text for text in texts if re.search('[가-힣]', text) and text != '한국어']
for choice in ['dark', 'light'] * 5:
    apply_theme(app, choice)
    app.processEvents()
    assert (dialog.palette().color(QPalette.Window).lightness() < 128) == (choice == 'dark')
    for combo in dialog.findChildren(QComboBox):
        combo.showPopup()
        app.processEvents()
        assert (combo.view().palette().color(QPalette.Base).lightness() < 128) == (choice == 'dark')
        combo.hidePopup()
for item in extra:
    item.reject()
window.quit()
window.pool.waitForDone()
window.usage.pool.waitForDone()
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('frozen', [False, True])
def test_restart_command_preserves_data_directory(tmp_path, monkeypatch, frozen):
    from usagedesk import app
    calls = []
    monkeypatch.setattr(sys, 'frozen', frozen, raising=False)
    monkeypatch.setattr(app.subprocess, 'Popen', lambda *a, **kw: calls.append((a, kw)))
    app.restart_application(tmp_path)
    command = calls[0][0][0]
    assert command[-3:] == ['--data-dir', str(tmp_path), '--window']
    assert ('-m' in command) is not frozen
    assert calls[0][1]['creationflags'] == subprocess.CREATE_NO_WINDOW


def test_restart_failure_is_reported(tmp_path, monkeypatch):
    from usagedesk import app
    messages = []
    def fail(*a, **kw):
        raise OSError('Launch failed')
    monkeypatch.setattr(app.subprocess, 'Popen', fail)
    monkeypatch.setattr(app.QMessageBox, 'critical', lambda *a: messages.append(a))
    app.restart_application(tmp_path)
    assert messages and '재시작' in messages[0][2]


@pytest.mark.parametrize('failed', [True, False])
def test_language_page_restarts_only_after_save_and_preserves_settings(tmp_path, monkeypatch, failed):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QWidget

    from usagedesk import language_ui
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.store = SimpleNamespace(directory=tmp_path)
    quits = []
    owner.quit = lambda: quits.append(True)
    page = language_ui.LanguagePage(owner)
    page.choice.setCurrentIndex(page.choice.findData('en'))
    files = ['display.ini', 'config.json', 'hotkeys.json']
    for name in files:
        (tmp_path / name).write_bytes(b'preserved test settings')
    if failed:
        def fail(*args):
            raise OSError('Write failed')
        monkeypatch.setattr(language_ui, 'save_language', fail)
    page.apply()
    app.processEvents()
    assert bool(quits) is not failed
    assert getattr(owner, 'restart_requested', False) is not failed
    if not failed:
        assert json.loads((tmp_path / 'language.json').read_text()) == {'language': 'en', 'appearance': 'system'}
    for name in files:
        assert (tmp_path / name).read_bytes() == b'preserved test settings'
    owner.deleteLater()
    app.processEvents()


def test_standard_qt_translation_keeps_untranslated_labels():
    translator = i18n.StandardButtons()
    for value in ('ko', 'en'):
        i18n._language = value
        try:
            assert translator.translate('Unrelated', 'Hello') == 'Hello'
            assert translator.translate('QDialogButtonBox', 'Cancel') == ('취소' if value == 'ko' else 'Cancel')
            assert translator.translate('QDialogButtonBox', 'Unknown') == 'Unknown'
        finally:
            i18n._language = 'ko'


def test_language_change_restarts_real_application_without_lock_conflict(tmp_path):
    code = r'''
import json,sys
from pathlib import Path
from PySide6.QtCore import QTimer
from usagedesk import app, ui
original = ui.MainWindow
class AutoChange(original):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        def change():
            self.language_page.choice.setCurrentIndex(self.language_page.choice.findData('en'))
            self.language_page.apply()
        QTimer.singleShot(50, change)
ui.MainWindow = AutoChange
spawn = app.subprocess.Popen
children = []
def restart(command, **kwargs):
    child = spawn(command + ['--smoke-ms', '500'], **kwargs)
    children.append(child)
    return child
app.subprocess.Popen = restart
directory = Path(sys.argv[1])
sys.argv = ['usagedesk', '--data-dir', str(directory), '--window']
assert app.main() == 0
assert len(children) == 1
try:
    assert children[0].wait(timeout=20) == 0
finally:
    if children[0].poll() is None:
        children[0].kill()
        children[0].wait()
assert json.loads((directory / 'language.json').read_text())['language'] == 'en'
assert not (directory / 'instance.lock').exists()
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
