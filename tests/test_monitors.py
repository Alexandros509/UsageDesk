from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from usagedesk.bar import DisplayDialog, DisplayOptions, DisplayStore, UsageBar
from usagedesk.monitors import choose_screen, screen_id, screen_label
from usagedesk.usage_ui import UsagePane


class Screen:
    def __init__(self, name, area, serial, scale=1):
        self._name, self.area, self.serial, self.scale = name, area, serial, scale

    def name(self):
        return self._name

    def manufacturer(self):
        return 'Test displays'

    def model(self):
        return 'Test panel'

    def serialNumber(self):
        return self.serial

    def devicePixelRatio(self):
        return self.scale

    def geometry(self):
        return self.area

    def availableGeometry(self):
        return self.area.adjusted(0, 0, 0, -40)


def screens():
    return [Screen('DISPLAY1', QRect(0, 0, 1707, 1067), 'test-laptop', 1.5),
            Screen('DISPLAY2', QRect(-1920, 0, 1920, 1080), 'test-external')]


def test_stable_identity_survives_renumber_and_does_not_store_serial():
    laptop, external = screens()
    changed = Screen('DISPLAY9', QRect(3000, -1000, 1920, 1080), external.serial)
    assert screen_id(external) == screen_id(changed)
    assert screen_id(laptop) != screen_id(external)
    assert external.serial not in screen_id(external) and len(screen_id(external)) == 64


def test_interface_identity_when_serial_is_unavailable(monkeypatch):
    import usagedesk.monitors as monitors
    a = Screen('DISPLAY1', QRect(0, 0, 1920, 1080), '')
    b = Screen('DISPLAY7', a.area, '')
    monkeypatch.setattr(monitors, 'device_interface', lambda _: 'stable-test-device')
    assert screen_id(a) == screen_id(b)
    monkeypatch.setattr(monitors, 'device_interface', lambda name: name)
    assert screen_id(a) != screen_id(b)


@pytest.mark.parametrize('mode', ['last', 'selected'])
def test_selection_reconnect_and_changed_primary(mode):
    laptop, external = screens()
    identifier = screen_id(external)
    assert choose_screen([external, laptop], laptop, mode, identifier) is external
    assert choose_screen([laptop], laptop, mode, identifier) is laptop
    assert choose_screen([laptop, external], laptop, mode, identifier) is external
    assert choose_screen([laptop, external], external, 'primary', screen_id(laptop)) is external
    assert choose_screen([], None, mode, identifier) is None


def test_legacy_position_and_negative_screen_coordinates():
    laptop, external = screens()
    assert choose_screen([laptop, external], laptop, 'last', '', QPoint(-1500, 20)) is external
    assert choose_screen([laptop], laptop, 'last', '', QPoint(-1500, 20)) is laptop
    assert '2560×1600' in screen_label(laptop, laptop)
    assert '주 모니터' in screen_label(laptop, laptop)


@pytest.mark.parametrize('mode', ['last', 'primary', 'selected'])
@pytest.mark.parametrize('returning', [True, False])
def test_monitor_options_roundtrip_and_old_settings_default(tmp_path, mode, returning):
    store = DisplayStore(tmp_path)
    assert store.load().monitor_mode == 'last'
    value = DisplayOptions(monitor_mode=mode, monitor_id=screen_id(screens()[1]),
                           return_to_monitor=returning)
    store.save(value)
    assert DisplayStore(tmp_path).load() == value
    store.settings.setValue('monitor_mode', 'unknown')
    store.settings.setValue('monitor_id', 'invalid/path')
    assert store.load().monitor_mode == 'last' and store.load().monitor_id == ''


@pytest.fixture
def monitor_bar(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.quitting = False
    owner.tray_menu = QMenu(owner)
    pane = UsagePane(tmp_path, autoload=False)
    bar = UsageBar(owner, pane, tmp_path)
    active = screens()
    monkeypatch.setattr(bar, 'screens', lambda: list(active))
    monkeypatch.setattr(bar, 'primary_screen', lambda: active[0])
    yield bar, pane, owner, app, active
    bar.dock.remove()
    bar.hide()
    pane.shutdown()
    pane.pool.waitForDone()
    owner.deleteLater()
    pane.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('placement', ['top', 'bottom', 'floating'])
def test_quick_move_preserves_placement_and_remembers_target(monitor_bar, placement):
    bar, _, _, app, active = monitor_bar
    bar.options = replace(bar.options, placement=placement)
    bar.show_bar()
    app.processEvents()
    menu = QMenu()
    bar.populate_monitor_menu(menu)
    assert len(menu.actions()) == 2 and sum(a.isChecked() for a in menu.actions()) == 1
    menu.actions()[1].trigger()
    app.processEvents()
    assert bar.options.placement == placement
    assert bar.options.monitor_mode == 'selected'
    assert bar.store.load().monitor_id == screen_id(active[1])
    assert bar.target_screen() is active[1]
    if placement == 'top':
        assert bar.x() == -1920 and bar.y() == 0
    elif placement == 'bottom':
        assert bar.geometry().bottom() == active[1].geometry().bottom()
    else:
        assert active[1].availableGeometry().contains(bar.geometry())
    bar.populate_monitor_menu(menu)
    assert menu.actions()[1].isChecked()
    menu.deleteLater()


@pytest.mark.parametrize('returning', [True, False])
def test_monitor_unplug_and_reconnect_policy(monitor_bar, returning):
    bar, _, _, app, active = monitor_bar
    external = active[1]
    bar.options = replace(bar.options, placement='top', monitor_mode='selected',
                          monitor_id=screen_id(external), return_to_monitor=returning)
    bar.show_bar()
    app.processEvents()
    active.pop()
    bar.monitors_changed()
    app.processEvents()
    assert bar.target_screen() is active[0] and bar.x() == 0
    assert bar.options.monitor_id == screen_id(external if returning else active[0])
    active.append(external)
    bar.monitors_changed()
    app.processEvents()
    assert bar.target_screen() is (external if returning else active[0])


def test_missing_target_at_startup_with_return_disabled_is_not_restored_later(monitor_bar):
    bar, _, _, app, active = monitor_bar
    external = active.pop()
    bar.options = replace(bar.options, monitor_mode='selected', monitor_id=screen_id(external),
                          return_to_monitor=False)
    bar.show_bar()
    app.processEvents()
    assert bar.store.load().monitor_id == screen_id(active[0])
    active.append(external)
    bar.monitors_changed()
    app.processEvents()
    assert bar.target_screen() is active[0]


def test_tray_and_restart_preserve_selected_monitor(monitor_bar, tmp_path, monkeypatch):
    bar, pane, owner, app, active = monitor_bar
    bar.options = replace(bar.options, placement='bottom')
    bar.show_bar()
    bar.move_to_monitor(screen_id(active[1]))
    bar.tray_only()
    bar.restore_bar()
    app.processEvents()
    assert bar.target_screen() is active[1] and bar.options.placement == 'bottom'
    bar.dock.remove()
    bar.hide()
    restored = UsageBar(owner, pane, tmp_path)
    monkeypatch.setattr(restored, 'screens', lambda: active)
    monkeypatch.setattr(restored, 'primary_screen', lambda: active[0])
    try:
        restored.show_bar()
        app.processEvents()
        assert restored.target_screen() is active[1]
        assert restored.options.placement == 'bottom'
        assert restored.geometry().bottom() == active[1].geometry().bottom()
    finally:
        restored.dock.remove()
        restored.hide()
        restored.deleteLater()


def test_move_storage_failure_or_disconnected_action_keeps_existing_state(monitor_bar, monkeypatch):
    bar, _, _, _, active = monitor_bar
    before = bar.options
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a))
    assert not bar.move_to_monitor('missing')
    monkeypatch.setattr(bar.store, 'save', lambda _: (_ for _ in ()).throw(OSError()))
    assert not bar.move_to_monitor(screen_id(active[1]))
    assert bar.options == before and len(warnings) == 2


def test_settings_monitor_controls_and_quick_move_preserve_other_drafts(monitor_bar):
    bar, _, owner, _, active = monitor_bar
    dialog = DisplayDialog(owner, bar.options, {})
    owner.display_editor = dialog
    dialog.theme.setCurrentIndex(dialog.theme.findData('mono'))
    try:
        assert not dialog.monitor_screen.isEnabled()
        dialog.monitor_mode.setCurrentIndex(dialog.monitor_mode.findData('selected'))
        assert dialog.monitor_screen.isEnabled()
        assert bar.move_to_monitor(screen_id(active[1]))
        assert dialog.theme.currentData() == 'mono'
        assert dialog.value().monitor_id == screen_id(active[1])
        dialog.monitor_mode.setCurrentIndex(dialog.monitor_mode.findData('primary'))
        assert not dialog.return_to_monitor.isEnabled()
    finally:
        dialog.deleteLater()


def test_hidden_bar_move_does_not_exit_tray_mode(monitor_bar):
    bar, _, _, _, active = monitor_bar
    bar.options = replace(bar.options, tray_mode=True, placement='top')
    assert bar.move_to_monitor(screen_id(active[1]))
    assert bar.options.tray_mode and not bar.isVisible()


def test_primary_preference_follows_changed_primary(monitor_bar):
    bar, _, _, app, active = monitor_bar
    bar.options = replace(bar.options, placement='top', monitor_mode='primary')
    bar.show_bar()
    app.processEvents()
    active.reverse()
    bar.monitors_changed()
    app.processEvents()
    assert bar.target_screen() is active[0]
    assert bar.geometry().topLeft() == active[0].geometry().topLeft()


def test_disconnected_selection_is_preserved_in_settings():
    app = QApplication.instance() or QApplication([])
    identifier = screen_id(screens()[1])
    options = DisplayOptions(monitor_mode='selected', monitor_id=identifier)
    dialog = DisplayDialog(None, options, {})
    try:
        assert dialog.value().monitor_id == identifier
        assert '연결 안 됨' in dialog.monitor_screen.currentText()
        assert dialog.value().return_to_monitor
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_stale_menu_action_does_not_move_to_a_disconnected_screen(monitor_bar, monkeypatch):
    bar, _, _, _, active = monitor_bar
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a))
    menu = QMenu()
    bar.populate_monitor_menu(menu)
    before = bar.options
    active.pop()
    menu.actions()[1].trigger()
    assert bar.options == before and warnings
    bar.populate_monitor_menu(menu)
    assert len(menu.actions()) == 1
    menu.deleteLater()


def test_old_appbar_is_released_before_cross_monitor_move(monitor_bar, monkeypatch):
    bar, _, _, _, active = monitor_bar
    bar.options = replace(bar.options, placement='top')
    bar.show_bar()
    events = []
    original_place = bar.place_on_screen

    def place(screen, position=None):
        events.append('move')
        original_place(screen, position)

    monkeypatch.setattr(bar, 'place_on_screen', place)
    monkeypatch.setattr(bar.dock, 'send', lambda message, data=None: events.append(message) or 1)
    bar.dock.registered = True
    assert bar.move_to_monitor(screen_id(active[1]))
    assert events[0] == 1 and events.index(1) < events.index('move')


@pytest.mark.parametrize('message, handler', [(0x02E0, 'relayout'), (0x007E, 'monitors_changed')])
def test_dpi_change_keeps_registration_while_topology_change_reselects_monitor(
        monitor_bar, monkeypatch, message, handler):
    import ctypes
    from ctypes import wintypes

    from usagedesk import appbar

    bar, _, _, _, _ = monitor_bar
    calls = []
    monkeypatch.setattr(bar, 'relayout', lambda: calls.append('relayout'))
    monkeypatch.setattr(bar, 'monitors_changed', lambda: calls.append('monitors_changed'))
    monkeypatch.setattr(appbar.QTimer, 'singleShot', lambda delay, callback: callback())
    bar.dock.callback = 0xC001
    bar.dock.taskbar_created = 0xC002
    msg = wintypes.MSG()
    msg.message = message
    bar.dock.native_event(ctypes.addressof(msg))
    assert calls == [handler]


def test_fallback_save_failure_still_honors_no_return_for_this_session(monitor_bar, monkeypatch):
    bar, _, _, app, active = monitor_bar
    external = active[1]
    bar.options = replace(bar.options, monitor_mode='selected', monitor_id=screen_id(external),
                          return_to_monitor=False)
    bar.show_bar()
    app.processEvents()
    monkeypatch.setattr(bar.store, 'save', lambda _: (_ for _ in ()).throw(OSError()))
    active.pop()
    bar.monitors_changed()
    app.processEvents()
    assert bar.options.monitor_id == screen_id(active[0])
    active.append(external)
    bar.monitors_changed()
    app.processEvents()
    assert bar.target_screen() is active[0]
