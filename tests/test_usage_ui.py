import time
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from usagedesk.domain import QuotaLimit
from usagedesk.oauth import CONFIGS, ProviderError
from usagedesk.providers import Snapshot, Tokens
from usagedesk.usage_ui import GrokLoginDialog, LoginDialog, UsagePane


@pytest.mark.parametrize("provider", ["claude", "codex", "grok"])
@pytest.mark.parametrize("index,method,arguments", [
    (0, "login", ()), (1, "refresh", (True,)), (2, "disconnect", ()),
])
def test_account_buttons_dispatch_provider_key(tmp_path, monkeypatch, provider, index, method,
                                              arguments):
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    calls = []
    monkeypatch.setattr(pane, method, lambda *args: calls.append(args))
    try:
        button = pane.cards[provider][2][index]
        button.setEnabled(True)
        QTest.mouseClick(button, Qt.LeftButton)
        assert calls == [(provider, *arguments)]
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


def pump(app, pane):
    end = time.monotonic() + 3
    while pane.tasks and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert not pane.tasks


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_click_opens_login_dialog_and_launches_browser_authorization(tmp_path, monkeypatch, provider):
    app = QApplication.instance() or QApplication([])
    # Use a real local callback listener on an ephemeral port; never open the user's browser.
    monkeypatch.setitem(CONFIGS, provider, replace(CONFIGS[provider], ports=(0,)))
    opened, failures, listeners = [], [], []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    pane = UsagePane(tmp_path, autoload=False)
    pane.show()

    def interact():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, LoginDialog)
            assert dialog.config.key == provider
            QTest.mouseClick(dialog.start_button, Qt.LeftButton)
            assert dialog.listener is not None
            listeners.append(dialog.listener)
            assert "기다리는 중" in dialog.status.text()
        except Exception as exc:
            failures.append(exc)
        finally:
            if dialog is not None:
                dialog.reject()

    try:
        QTimer.singleShot(0, interact)
        QTest.mouseClick(pane.cards[provider][2][0], Qt.LeftButton)
        app.processEvents()
        assert not failures, failures
        assert len(opened) == 1
        url = urlsplit(opened[0])
        assert url.scheme == "https"
        assert url.netloc == urlsplit(CONFIGS[provider].authorize_url).netloc
        query = parse_qs(url.query)
        assert query["code_challenge_method"] == ["S256"]
        assert query["state"]
        assert urlsplit(query["redirect_uri"][0]).hostname == "localhost"
        assert pane.sessions[provider].tokens is None
        pump(app, pane)
    finally:
        for listener in listeners:
            listener.close()
            listener.thread.join(timeout=2)
        pane.shutdown()
        pane.pool.waitForDone()
        pane.hide()
        pane.deleteLater()
        app.processEvents()


def test_grok_dialog_connects_existing_cli_and_disconnect_clears_quota(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions["grok"]
    called = []

    def connect(epoch):
        called.append(epoch)
        session.tokens = True
        return Snapshot(datetime.now(UTC), [QuotaLimit("account", "통합 주간 한도", 100)])

    monkeypatch.setattr(session, "connect", connect)

    def accept():
        dialog = app.activeModalWidget()
        if isinstance(dialog, GrokLoginDialog):
            buttons = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        elif dialog:
            dialog.reject()

    try:
        QTimer.singleShot(0, accept)
        QTest.mouseClick(pane.cards["grok"][2][0], Qt.LeftButton)
        pump(app, pane)
        assert called == [session.epoch]
        assert pane.snapshots["grok"].limits[0].used_percent == 100
        assert "마지막 성공 조회" in pane.cards["grok"][0].text()
        pane.disconnect("grok")
        assert "grok" not in pane.snapshots
        assert session.tokens is None
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


def test_live_controller_shows_snapshot_preserves_stale_and_logout(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions["claude"]
    session.tokens = Tokens("fake-access", "fake-refresh")
    pane.policies["claude"].reconnect()
    snapshot = Snapshot(datetime.now(UTC), [QuotaLimit("short", "5시간", 42.5)])
    monkeypatch.setattr(session, "fetch", lambda _: snapshot)
    try:
        pane.refresh("claude", True)
        pump(app, pane)
        assert pane.snapshots["claude"] == snapshot
        assert "마지막 성공 조회" in pane.cards["claude"][0].text()
        assert "42.5%" in pane.cards["claude"][1].itemAt(0).widget().text()

        def fail(_):
            raise ProviderError("RATE_LIMITED", 3600)

        monkeypatch.setattr(session, "fetch", fail)
        pane.policies["claude"].last_start -= 11
        pane.refresh("claude", True)
        pump(app, pane)
        assert pane.snapshots["claude"] == snapshot
        assert "이전 데이터" in pane.cards["claude"][0].text()
        assert not pane.cards["claude"][2][1].isEnabled()
        assert not pane.cards["claude"][2][0].isEnabled()
        pane.disconnect("claude")
        assert "claude" not in pane.snapshots
        assert pane.sessions["claude"].tokens is None
        assert pane.cards["claude"][1].itemAt(0).widget().text() == "—"
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


def test_grok_missing_usage_keeps_previous_value_and_recovers(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions['grok']
    session.tokens = True
    pane.policies['grok'].reconnect()
    previous = Snapshot(datetime.now(UTC), [QuotaLimit('account', '주간 한도', 100)])
    pane.snapshots['grok'] = previous
    def unavailable(_):
        raise ProviderError('USAGE_UNAVAILABLE')
    monkeypatch.setattr(session, 'fetch', unavailable)
    try:
        pane.refresh('grok', True)
        pump(app, pane)
        assert pane.snapshots['grok'] is previous
        assert '현재 사용량 값이 없습니다' in pane.cards['grok'][0].text()
        assert '다음 자동 조회' in pane.cards['grok'][0].text()
        assert pane.cards['grok'][2][0].isEnabled()
        pane.policies['grok'].last_start -= 11
        pane.render()
        assert pane.cards['grok'][2][1].isEnabled()
        current = Snapshot(datetime.now(UTC), [QuotaLimit('account', '주간 한도', 1)])
        monkeypatch.setattr(session, 'fetch', lambda _: current)
        pane.refresh('grok', True)
        pump(app, pane)
        assert pane.snapshots['grok'] is current
        assert 'grok' not in pane.errors
        assert '마지막 성공 조회' in pane.cards['grok'][0].text()
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()
