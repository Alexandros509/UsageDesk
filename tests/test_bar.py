from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QHelpEvent, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QStyleFactory, QToolTip, QWidget

from usagedesk.bar import (
    DisplayDialog,
    DisplayOptions,
    DisplayStore,
    UsageBar,
    display_percent,
    keep_on_screen,
    quota_caption,
    reset_countdown,
    visible_limits,
)
from usagedesk.domain import QuotaLimit
from usagedesk.launcher import LauncherEntry
from usagedesk.providers import Snapshot, normalize_usage
from usagedesk.usage_ui import UsagePane


@pytest.fixture
def bar(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    owner = QWidget()
    owner.quitting = False
    owner.opened = []
    owner.open_tab = owner.opened.append
    owner.tray_menu = QMenu(owner)
    pane = UsagePane(tmp_path, autoload=False)
    widget = UsageBar(owner, pane, tmp_path)
    yield widget, pane, owner, app
    pane.shutdown()
    pane.pool.waitForDone()
    widget.hide()
    owner.deleteLater()
    pane.deleteLater()
    app.processEvents()


def test_display_preferences_and_position_survive_restart(tmp_path):
    store = DisplayStore(tmp_path)
    value = DisplayOptions("compact", "mono", "dark", False, True, "custom", ("claude/a",))
    store.save(value)
    store.save_position(QPoint(-1200, 80))
    restored = DisplayStore(tmp_path)
    assert restored.load() == value
    assert restored.position() == QPoint(-1200, 80)
    store.settings.setValue("size", "bad")
    store.settings.setValue("percentages", False)
    assert store.load().size == "standard"
    assert store.load().percentages  # Never allow a blank, inaccessible bar.


def test_filters_keep_real_zero_and_never_invent_missing():
    snapshot = Snapshot(datetime.now(UTC), [QuotaLimit("zero", "Zero", 0),
                                          QuotaLimit("none", "Missing", None)])
    assert [q.key for q in visible_limits(snapshot, "claude", DisplayOptions())] == ["zero"]
    custom = DisplayOptions(mode="custom", selected=("codex/zero",))
    assert not visible_limits(snapshot, "claude", custom)
    assert len(visible_limits(snapshot, "codex", custom)) == 1


def test_remaining_and_reset_options_persist_and_change_display(bar, tmp_path, monkeypatch):
    widget, pane, _, _ = bar
    monkeypatch.setattr(widget, "available_geometry", lambda: QRect(0, 0, 1920, 1080))
    now = datetime.now(UTC)
    quota = QuotaLimit("five_hour", "5시간", 80, now + timedelta(hours=3), 18000)
    options = replace(widget.options, quota_display="remaining", reset_display="inline")
    store = DisplayStore(tmp_path)
    store.save(options)
    assert store.load() == options
    assert display_percent(quota, options) == 20
    assert quota_caption(quota, options, now) == "5h │ 초기화 3hr 0min"
    assert display_percent(replace(quota, used_percent=120), options) == 0
    pane.snapshots["claude"] = Snapshot(now, [quota])
    widget.options = options
    widget.refresh()
    assert any(item[2] == "20%" for item in widget.items)
    assert quota.used_percent == 80  # Display preference never mutates source or warning thresholds.
    dialog = DisplayDialog(widget, options, pane.snapshots)
    assert dialog.value().quota_display == "remaining"
    assert dialog.value().reset_display == "inline"
    dialog.deleteLater()


@pytest.mark.parametrize("seconds,expected", [(8*86400, "1wk 1d"), (2*86400+3*3600, "2d 3hr"),
                                             (3*3600+20*60, "3hr 20min"), (45*60, "45min"),
                                             (25, "<1min"), (0, "초기화 확인 중")])
def test_countdown_units_and_boundaries(seconds, expected):
    now = datetime.now(UTC)
    quota = QuotaLimit("a", "A", 0, now + timedelta(seconds=seconds))
    assert reset_countdown(quota, now) == expected
    assert reset_countdown(replace(quota, reset_at_utc=None), now) == "시간 미제공"


def test_grok_logo_quota_and_settings_are_available(bar, monkeypatch):
    widget, pane, _, _ = bar
    monkeypatch.setattr(widget, "available_geometry", lambda: QRect(0, 0, 1920, 1080))
    pane.snapshots["grok"] = Snapshot(datetime.now(UTC), [
        QuotaLimit("account", "통합 주간 한도", 100, window_seconds=604800),
    ])
    pane.render()
    assert not widget.service_images["grok"].isNull()
    item = next(i for i in widget.items if i[4] == "grok" and i[3])
    assert item[2] == "100%"
    assert "통합 주간 한도" in widget.tooltip_at(item[0].center())[0]
    options = replace(widget.options, mode="custom", selected=("grok/account",))
    dialog = DisplayDialog(widget, options, pane.snapshots)
    assert dialog.checks["grok/account"].isChecked()
    dialog.deleteLater()


@pytest.mark.parametrize("mode,expected", [("used", "100%"), ("remaining", "0%")])
@pytest.mark.parametrize("extra", [False, True])
def test_weekly_exhaustion_adjusts_five_hour_even_when_weekly_hidden(
        bar, monkeypatch, mode, expected, extra):
    widget, pane, _, app = bar
    monkeypatch.setattr(widget, "available_geometry", lambda: QRect(0, 0, 1920, 1080))
    now = datetime.now(UTC)
    snapshot = normalize_usage("claude", {
        "five_hour": {"utilization": 0, "resets_at": None},
        "seven_day": {"utilization": 100, "resets_at": (now + timedelta(hours=2)).isoformat()},
        "extra_usage": {"is_enabled": extra},
    }, now)
    pane.snapshots["claude"] = snapshot
    widget.options = replace(widget.options, mode="custom", selected=("claude/five_hour",),
                             quota_display=mode, reset_display="off")
    pane.render()
    item = next(i for i in widget.items if i[4] == "claude" and i[3])
    assert item[2] == expected
    assert snapshot.limits[0].used_percent == 0
    assert snapshot.limits[0].remaining_percent == 100
    caption = quota_caption(item[3], widget.options, provider="claude", snapshot=snapshot)
    assert caption.startswith("5h │ 주간 한도 소진")
    assert ("추가 사용 활성" in caption) == extra
    tooltip = widget.tooltip_at(item[0].center(), now)[0]
    assert "5시간 한도 자체: 사용 0% · 남음 100%" in tooltip
    assert "주간 초기화 예정" in tooltip
    assert ("추가 사용 활성" in tooltip) == extra
    assert "주간 한도 소진" in widget.accessibleName()
    widget.show()
    app.processEvents()
    assert not widget.grab().isNull()  # Exercise the actual ring/caption painting path.
    snapshot.limits[1] = replace(snapshot.limits[1], used_percent=20)
    pane.render()
    restored = next(i for i in widget.items if i[4] == "claude" and i[3])
    assert restored[2] == ("0%" if mode == "used" else "100%")
    assert "주간 한도 소진" not in widget.tooltip_at(restored[0].center(), now)[0]


@pytest.mark.parametrize("provider,key,used", [
    ("claude", "seven_day", 99.9), ("claude", "seven_day", None),
    ("claude", "seven_day_sonnet", 100), ("codex", "seven_day", 100),
    ("grok", "seven_day", 100),
])
def test_partial_unknown_scoped_and_other_service_limits_do_not_override(provider, key, used):
    quota = QuotaLimit("five_hour", "5시간", 25, window_seconds=18000)
    snapshot = Snapshot(datetime.now(UTC), [quota, QuotaLimit(key, "주간", used)])
    options = DisplayOptions(quota_display="remaining")
    assert display_percent(quota, options, provider=provider, snapshot=snapshot) == 75
    assert "소진" not in quota_caption(quota, options, provider=provider, snapshot=snapshot)


def test_bar_updates_stale_data_and_clears_on_disconnect(bar):
    widget, pane, _, _ = bar
    pane.snapshots["claude"] = Snapshot(datetime.now(UTC) - timedelta(minutes=7), [
        QuotaLimit("five_hour", "5시간", 0, window_seconds=18000),
    ])
    pane.render()
    assert any(item[2] == "0%" for item in widget.items)
    assert any(item[2] == "Claude" for item in widget.items)
    assert not any(" !" in item[2] for item in widget.items)
    assert "이전 데이터" in next(item[5] for item in widget.items if item[3])
    pane.disconnect("claude")
    assert not any(item[3] for item in widget.items)
    assert any(item[2] == "로그인" for item in widget.items)


def test_bar_click_opens_detail_without_starting_auth_or_requests(bar, monkeypatch):
    widget, pane, owner, app = bar
    calls = []
    monkeypatch.setattr(pane, "refresh_all", lambda: calls.append("refresh"))
    widget.show_bar()
    app.processEvents()
    QTest.mouseClick(widget, Qt.LeftButton, pos=widget.items[0][0].center())
    assert owner.opened == [0]
    assert not pane.tasks
    refresh = next(item[0] for item in widget.items if item[1] == "refresh")
    QTest.mouseClick(widget, Qt.LeftButton, pos=refresh.center())
    assert calls == ["refresh"]


@pytest.mark.parametrize("placement", ["floating", "top"])
def test_inactive_bar_tooltip_routes_to_gauge_without_mouse_move(bar, monkeypatch, placement):
    widget, pane, owner, app = bar
    widget.options = replace(widget.options, placement=placement)
    pane.snapshots["claude"] = Snapshot(datetime.now(UTC), [
        QuotaLimit("five_hour", "5시간", 42, datetime.now(UTC) + timedelta(hours=2), 18000),
    ])
    pane.render()
    widget.show_bar()
    owner.show()
    owner.activateWindow()
    app.processEvents()
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: shown.append(args))
    rect = next(item[0] for item in widget.items if item[3])
    event = QHelpEvent(QEvent.ToolTip, rect.center(), widget.mapToGlobal(rect.center()))
    app.sendEvent(widget, event)
    assert widget.testAttribute(Qt.WA_AlwaysShowToolTips)
    assert event.isAccepted()
    assert len(shown) == 1
    assert "사용 42% · 남음 58%" in shown[0][1]
    assert "초기화 " in shown[0][1] and "남음" in shown[0][1]
    assert shown[0][3] == rect  # Moving to another gauge ends the old tooltip.
    assert owner.opened == []


def test_tooltip_uses_current_time_and_never_invents_reset(bar):
    widget, pane, _, _ = bar
    now = datetime.now(UTC)
    pane.snapshots["codex"] = Snapshot(now, [
        QuotaLimit("primary_window", "5시간", 20, now + timedelta(hours=2), 18000),
        QuotaLimit("unknown", "추가", 0),
    ])
    pane.render()
    quotas = [item for item in widget.items if item[3]]
    assert "2시간 0분 남음" in widget.tooltip_at(quotas[0][0].center(), now)[0]
    assert "초기화 확인 대기" in widget.tooltip_at(
        quotas[0][0].center(), now + timedelta(hours=3))[0]
    assert "초기화 시간 미제공" in widget.tooltip_at(quotas[1][0].center(), now)[0]


def test_many_limits_overflow_with_all_controls_accessible(bar, monkeypatch):
    widget, pane, _, _ = bar
    monkeypatch.setattr(widget, "available_geometry", lambda: QRect(0, 0, 640, 480))
    for provider in ("claude", "codex"):
        pane.snapshots[provider] = Snapshot(datetime.now(UTC), [
            QuotaLimit(str(i), "아주 긴 모델 이름 " + str(i), 100.5) for i in range(30)
        ])
    pane.render()
    assert widget.width() <= 640
    assert sum(item[2].startswith("+") for item in widget.items) == 2
    assert widget.items[-1][1] == "menu"
    assert widget.rect().contains(widget.items[-1][0])
    widget.options = replace(widget.options, size="prominent", percentages=False)
    widget.refresh()
    assert widget.width() <= 640


def test_position_recovers_from_disconnected_monitor_and_handles_negative_origin():
    area = QRect(-1920, 40, 1920, 1040)
    assert keep_on_screen(QRect(9999, -9999, 500, 42), area) == QPoint(-500, 40)
    assert keep_on_screen(QRect(-2500, 2000, 500, 42), area) == QPoint(-1920, 1038)


def test_unavailable_saved_monitor_restores_visible_bar(bar):
    widget, _, _, app = bar
    widget.store.save_position(QPoint(99999, -99999))
    widget.show_bar()
    app.processEvents()
    assert QApplication.primaryScreen().availableGeometry().contains(widget.geometry())


@pytest.mark.parametrize("style", ["windows11", "windowsvista", "Windows", "Fusion"])
def test_popup_rows_are_readable_and_selectable_under_dark_system_palette(style):
    app = QApplication.instance() or QApplication([])
    previous_style = app.style().objectName()
    previous_palette = app.palette()
    if style not in QStyleFactory.keys():
        pytest.skip("Style unavailable")
    app.setStyle(style)
    dark = QPalette()
    for role in (QPalette.Base, QPalette.Window, QPalette.Button):
        dark.setColor(role, QColor("#202020"))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText, QPalette.HighlightedText):
        dark.setColor(role, QColor("white"))
    app.setPalette(dark)
    dialog = DisplayDialog(None, DisplayOptions(), {})
    try:
        dialog.show()
        app.processEvents()
        # The whole settings surface must stay light; native dark tabs/buttons
        # previously left large dark panels behind our dark text.
        surface = dialog.grab().toImage()
        samples = [surface.pixelColor(x, y).lightness()
                   for y in range(5, surface.height(), 4) for x in range(5, surface.width(), 4)]
        assert sum(value > 180 for value in samples) / len(samples) > .8
        for name in ("theme", "size", "appearance", "length", "placement", "mode"):
            combo = getattr(dialog, name)
            combo.showPopup()
            app.processEvents()
            view = combo.view()
            image = view.viewport().grab().toImage()
            for row in range(combo.count()):
                index = combo.model().index(row, 0)
                assert index.data()
                rect = view.visualRect(index).adjusted(12, 5, -12, -5)
                ink = sum(image.pixelColor(x, y).lightness() < 130
                          for y in range(max(0, rect.top()), min(image.height(), rect.bottom()))
                          for x in range(max(0, rect.left()), min(image.width(), rect.right())))
                assert ink > 10, (style, name, row, "Text is not visible")
            last = combo.model().index(combo.count() - 1, 0)
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(last).center())
            assert combo.currentIndex() == combo.count() - 1
            combo.hidePopup()
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()
        app.setStyle(previous_style)
        app.setPalette(previous_palette)


def test_settings_select_programs_persist_and_launch_only_on_bar_click(bar):
    widget, pane, owner, app = bar
    first = LauncherEntry("첫 번째", r"C:\apps\one.cmd", r"C:\apps")
    second = LauncherEntry("두 번째", r"C:\apps\two.cmd", r"C:\apps")
    owner.entries = [first, second]
    calls = []
    owner.launch = calls.append
    dialog = DisplayDialog(None, widget.options, {}, owner.entries)
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    app.processEvents()
    check = dialog.program_checks[second.id]
    QTest.mouseClick(check, Qt.LeftButton, pos=QPoint(8, check.height() // 2))
    options = dialog.value()
    assert options.programs == (second.id,)
    assert not calls
    widget.store.save(options)
    widget.options = widget.store.load()
    widget.refresh()
    assert widget.program_entries == [second]
    widget.show_bar()
    item = next(item for item in widget.items if item[1] == "program")
    QTest.mouseClick(widget, Qt.LeftButton, pos=item[0].center())
    assert calls == [second]
    assert all(not image.isNull() for image in widget.service_images.values())
    widget.options = replace(options, programs=())
    widget.refresh()
    assert not any(item[1] in ("program", "programs") for item in widget.items)
    dialog.hide()
    dialog.deleteLater()


def test_old_selected_claude_array_alias_still_selects_deduplicated_window(bar):
    _, _, _, app = bar
    snapshot = normalize_usage("claude", {
        "five_hour": {"utilization": 0},
        "limits": [{"kind": "session", "percent": 0}],
    })
    options = DisplayOptions(mode="custom", selected=("claude/limit:0",))
    assert [q.key for q in visible_limits(snapshot, "claude", options)] == ["five_hour"]
    dialog = DisplayDialog(None, options, {"claude": snapshot})
    assert dialog.checks["claude/five_hour"].isChecked()
    dialog.checks["claude/five_hour"].setChecked(False)
    assert not visible_limits(snapshot, "claude", dialog.value())
    assert "claude/limit:0" not in dialog.value().selected
    dialog.deleteLater()
    app.processEvents()


def test_many_selected_programs_fit_and_deleted_entries_disappear(bar, monkeypatch):
    widget, pane, owner, _ = bar
    monkeypatch.setattr(widget, "available_geometry", lambda: QRect(0, 0, 640, 480))
    owner.entries = [LauncherEntry("아주 긴 프로그램 이름 " + str(i),
                                   rf"C:\apps\{i}.cmd", r"C:\apps") for i in range(10)]
    widget.options = replace(widget.options, programs=tuple(e.id for e in owner.entries), size="prominent")
    for provider in ("claude", "codex"):
        pane.snapshots[provider] = Snapshot(datetime.now(UTC), [
            QuotaLimit(str(i), "주간 모델 한도", 30) for i in range(20)
        ])
    pane.render()
    assert widget.width() <= 640
    assert widget.hidden_programs
    assert widget.rect().contains(widget.items[-1][0])
    owner.entries = []
    widget.refresh()
    assert not widget.program_entries
    assert not any(item[1] in ("program", "programs") for item in widget.items)


def test_bar_length_modes_and_docking_preferences_persist(bar):
    widget, _, _, _ = bar
    widget.options = replace(widget.options, length="wide")
    widget.refresh()
    assert widget.width() >= min(800, widget.available_geometry().width() - 24)
    widget.options = replace(widget.options, length="full", placement="top")
    widget.store.save(widget.options)
    assert widget.store.load() == widget.options
    widget.refresh()
    assert widget.width() == widget.available_geometry().width()


def test_program_names_use_two_lines_without_eliding_and_use_spare_space(bar, monkeypatch):
    widget, _, owner, _ = bar
    monkeypatch.setattr(widget, "available_geometry", lambda: QRect(0, 0, 1920, 1080))
    owner.entries = [LauncherEntry(f"TIL MultiPlatform Application {i}",
                                   rf"C:\apps\{i}.cmd", r"C:\apps") for i in range(6)]
    widget.options = replace(widget.options, programs=tuple(e.id for e in owner.entries))
    widget.refresh()
    buttons = [item for item in widget.items if item[1] == "program"]
    assert len(buttons) == len(owner.entries)
    assert not widget.hidden_programs
    assert sum(item[0].width() for item in buttons) > 360
    assert widget.program_padding == 4
    assert widget.program_font.pointSize() == widget.font().pointSize() - 1
    metrics = QFontMetrics(widget.program_font)
    for item, entry in zip(buttons, owner.entries, strict=True):
        assert "\n" in item[2]
        assert "".join(item[2].split()) == "".join(entry.name.split())
        assert "…" not in item[2]
        assert max(metrics.horizontalAdvance(line) for line in item[2].split("\n")) <= (
            item[0].width() - widget.program_padding * 2)
        assert metrics.lineSpacing() * 2 <= item[0].height()
    assert widget.width() <= 1920
    assert not widget.grab().isNull()


def test_length_switch_preserves_reset_and_restores_gauge_geometry(bar, monkeypatch):
    widget, pane, _, _ = bar
    monkeypatch.setattr(widget, "available_geometry", lambda: QRect(0, 0, 2560, 1440))
    pane.snapshots["claude"] = Snapshot(datetime.now(UTC), [
        QuotaLimit("five_hour", "5h", 32, datetime.now(UTC) + timedelta(hours=3), 18000),
        QuotaLimit("seven_day", "7d", 65, datetime.now(UTC) + timedelta(days=2), 604800),
    ])
    widget.options = replace(widget.options, reset_display="inline")
    widget.refresh()
    original = [(QRect(i[0]), i[2]) for i in widget.items if i[3]]
    assert len(original) == 2
    for length in ("full", "wide", "auto"):
        dialog = DisplayDialog(None, widget.options, pane.snapshots)
        dialog.length.setCurrentIndex(dialog.length.findData(length))
        widget.options = dialog.value()
        widget.store.save(widget.options)
        widget.options = widget.store.load()
        widget.refresh()
        assert widget.options.reset_display == "inline"
        assert [(i[0], i[2]) for i in widget.items if i[3]] == original
        dialog.deleteLater()


def test_tray_mode_survives_restart_and_explicit_restore(bar):
    widget, _, owner, app = bar
    widget.show_bar()
    widget.tray_only()
    assert not widget.isVisible()
    assert widget.store.load().tray_mode
    widget.show_bar()  # Startup/close must not undo the preference.
    assert not widget.isVisible()
    widget.restore_bar()
    assert widget.isVisible()
    assert widget.store.load().placement == "floating"


@pytest.mark.parametrize("placement", ["top", "bottom"])
def test_dock_edge_and_preference(bar, placement):
    widget, _, _, app = bar
    widget.options = replace(widget.options, placement=placement)
    widget.store.save(widget.options)
    assert widget.store.load().placement == placement
    assert widget.dock.data().uEdge == (3 if placement == "bottom" else 1)
    widget.show_bar()
    app.processEvents()
    area = widget.screen().geometry()
    assert widget.y() == (area.bottom() - widget.height() + 1 if placement == "bottom" else area.top())


@pytest.mark.parametrize("placement", ["floating", "top", "bottom"])
@pytest.mark.parametrize("restart", [False, True])
def test_tray_restores_previous_placement_and_position(bar, placement, restart):
    widget, pane, owner, app = bar
    widget.options = replace(widget.options, placement=placement)
    widget.store.save(widget.options)
    widget.show_bar()
    app.processEvents()
    if placement == "floating":
        widget.move(35, 70)
    previous = widget.pos()
    widget.tray_only()
    widget.tray_only()  # Repeated hiding must not replace the saved position.
    saved = widget.store.load()
    assert saved.placement == placement and saved.tray_mode
    assert widget.store.position() == previous
    if restart:
        restored = UsageBar(owner, pane, Path(widget.store.settings.fileName()).parent)
        restored.show_bar()
        assert not restored.isVisible()
    else:
        restored = widget
    restored.restore_bar()
    app.processEvents()
    assert restored.options.placement == placement
    assert not restored.store.load().tray_mode
    assert restored.pos() == previous
    assert restored.isVisible()
    restored.hide()
    restored.dock.remove()


@pytest.mark.parametrize("placement", ["top", "bottom", "floating"])
def test_settings_tray_selection_keeps_underlying_placement(placement):
    app = QApplication.instance() or QApplication([])
    dialog = DisplayDialog(None, DisplayOptions(placement=placement), {})
    dialog.placement.setCurrentIndex(dialog.placement.findData("tray"))
    value = dialog.value()
    assert value.placement == placement and value.tray_mode
    reopened = DisplayDialog(None, value, {})
    assert reopened.value().placement == placement
    assert reopened.value().tray_mode
    dialog.deleteLater()
    reopened.deleteLater()
    app.processEvents()


def test_legacy_tray_setting_migrates_without_inventing_old_placement(tmp_path):
    store = DisplayStore(tmp_path)
    store.settings.setValue("placement", "tray")
    options = store.load()
    assert options.tray_mode and options.placement == "floating"
    store.save(options)
    assert store.load() == options


def test_unavailable_tray_settings_preserve_existing_placement(bar, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox
    widget, _, owner, _ = bar
    owner.tray_available = False
    before = widget.options
    widget.store.save(before)
    monkeypatch.setattr(DisplayDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(DisplayDialog, "value", lambda self: replace(before, tray_mode=True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    widget.settings_dialog()
    assert widget.options == before and widget.store.load() == before


def test_hide_unselected_services_persists_and_keeps_selected_provider(bar, tmp_path):
    widget, pane, _, _ = bar
    pane.snapshots['claude'] = Snapshot(datetime.now(UTC), [QuotaLimit('five_hour', '5h', 25)])
    widget.options = replace(widget.options, mode='custom', selected=(), hide_unselected=True)
    DisplayStore(tmp_path).save(widget.options)
    assert DisplayStore(tmp_path).load().hide_unselected
    widget.refresh()
    assert not [item for item in widget.items if item[1] == 'provider']
    assert any(item[1] == 'settings' for item in widget.items)
    widget.options = replace(widget.options, selected=('claude/five_hour',))
    widget.refresh()
    assert [item[4] for item in widget.items if item[1] == 'provider'] == ['claude']
    widget.options = replace(widget.options, selected=('codex/five_hour',))
    widget.refresh()
    assert [item[4] for item in widget.items if item[1] == 'provider'] == ['codex']
    widget.options = replace(widget.options, selected=(), hide_unselected=False)
    widget.refresh()
    assert len([item for item in widget.items if item[1] == 'provider']) == 3
    widget.options = replace(widget.options, mode='smart', hide_unselected=True)
    widget.refresh()
    assert len([item for item in widget.items if item[1] == 'provider']) == 3
    dialog = DisplayDialog(widget, widget.options, pane.snapshots)
    assert dialog.value().hide_unselected
    dialog.deleteLater()


def test_selected_service_survives_temporarily_missing_usage(bar):
    widget, pane, _, _ = bar
    pane.snapshots['claude'] = Snapshot(datetime.now(UTC), [QuotaLimit('five_hour', '5h', None)])
    widget.options = replace(widget.options, mode='custom', selected=('claude/five_hour',),
                             hide_unselected=True)
    widget.refresh()
    assert [item[4] for item in widget.items if item[1] == 'provider'] == ['claude']
