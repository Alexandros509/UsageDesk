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


@pytest.mark.parametrize("provider", ["claude", "codex", "grok", 'grokbot'])
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


def test_grokbot_web_dialog_connects_restores_and_disconnects(tmp_path, monkeypatch):
    import httpx

    from usagedesk.grokbot import USAGE_URL
    from usagedesk.providers import Transport
    from usagedesk.usage_ui import GrokBotLoginDialog
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'usagePercent': 38,
                                        'nextResetTimestampUtc': '2026-11-03T12:00:00Z'})

    transport = Transport(lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    pane.sessions['grokbot'].transport = transport
    failures = []

    def interact():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, GrokBotLoginDialog)
            dialog.tabs.setCurrentIndex(1)
            assert dialog.session_input.echoMode() == dialog.session_input.EchoMode.Password
            dialog.session_input.setText('test-user%3A%3Aa.b.c')
            dialog.accept()
            assert not dialog.session_input.text()
        except Exception as exc:
            failures.append(exc)
            if dialog:
                dialog.reject()

    try:
        QTimer.singleShot(0, interact)
        QTest.mouseClick(pane.cards['grokbot'][2][0], Qt.LeftButton)
        pump(app, pane)
        assert not failures
        assert len(requests) == 1 and str(requests[0].url) == USAGE_URL
        assert pane.snapshots['grokbot'].limits[0].used_percent == 38
        assert '로그인' not in pane.cards['grokbot'][0].text()
        pane.sessions['grokbot'].reset()
        pane.snapshots.clear()
        pane.restore('grokbot')
        pump(app, pane)
        assert pane.snapshots['grokbot'].limits[0].remaining_percent == 62
        pane.disconnect('grokbot')
        assert pane.sessions['grokbot'].tokens is None and 'grokbot' not in pane.snapshots
        assert not (tmp_path / 'secrets' / 'grokbot.dpapi').exists()
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


def test_grokbot_invalid_input_and_cancel_never_link_or_keep_secret(tmp_path):
    from usagedesk.usage_ui import GrokBotLoginDialog
    app = QApplication.instance() or QApplication([])
    dialog = GrokBotLoginDialog()
    try:
        dialog.tabs.setCurrentIndex(1)
        dialog.session_input.setText('invalid-cookie')
        dialog.accept()
        assert dialog.result() != dialog.DialogCode.Accepted and dialog.source is None
        assert '형식' in dialog.status.text()
        dialog.reject()
        assert dialog.cookie == '' and dialog.session_input.text() == ''
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_grokbot_auth_failure_keeps_previous_usage_and_stops_polling(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions['grokbot']
    session.tokens, session.source = True, 'session'
    pane.policies['grokbot'].reconnect()
    snapshot = Snapshot(datetime.now(UTC), [QuotaLimit('weekly', '주간 한도', 38)])
    pane.snapshots['grokbot'] = snapshot
    called = []

    def fail(_):
        called.append(True)
        raise ProviderError('AUTH_REQUIRED')

    monkeypatch.setattr(session, 'fetch', fail)
    try:
        pane.refresh('grokbot')
        pump(app, pane)
        assert pane.snapshots['grokbot'] is snapshot
        assert pane.policies['grokbot'].halted
        assert '만료' in pane.cards['grokbot'][0].text()
        for _ in range(3):
            pane.tick()
        assert called == [True]
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


def test_grokbot_unlinked_startup_does_not_read_other_app_credentials(tmp_path, monkeypatch):
    import usagedesk.grokbot as grokbot
    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(grokbot, 'read_cursor_session', lambda _: calls.append(True))
    pane = UsagePane(tmp_path, autoload=False)
    try:
        pane.restore('grokbot')
        pump(app, pane)
        assert not calls and pane.sessions['grokbot'].tokens is None
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('error_code,recover', [('AUTH_REQUIRED', True), ('RATE_LIMITED', False)])
def test_grokbot_local_signin_change_recovers_auth_but_never_bypasses_rate_limit(
        tmp_path, monkeypatch, error_code, recover):
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions['grokbot']
    session.tokens, session.source = True, 'cursor'
    revision, calls = [1], []
    monkeypatch.setattr(session, 'revision', lambda: tuple(revision))
    snapshot = Snapshot(datetime.now(UTC), [QuotaLimit('weekly', '주간 한도', 38)])

    def fetch(_):
        calls.append(True)
        if len(calls) == 1:
            raise ProviderError(error_code, 75 if error_code == 'RATE_LIMITED' else None)
        return snapshot

    monkeypatch.setattr(session, 'fetch', fetch)
    pane.policies['grokbot'].reconnect()
    try:
        pane.refresh('grokbot')
        pump(app, pane)
        revision[0] = 2
        pane.policies['grokbot'].last_start -= 11
        pane.tick()
        pump(app, pane)
        assert len(calls) == (2 if recover else 1)
        if recover:
            assert pane.snapshots['grokbot'] is snapshot and 'grokbot' not in pane.errors
        else:
            assert pane.errors['grokbot'] == 'RATE_LIMITED'
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('same_period', [True, False])
def test_grokbot_partial_response_does_not_reuse_old_period_percentage(
        tmp_path, monkeypatch, same_period):
    from datetime import timedelta
    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions['grokbot']
    session.tokens, session.source = True, 'session'
    reset = datetime.now(UTC) + timedelta(days=5)
    previous = Snapshot(datetime.now(UTC), [QuotaLimit('weekly', '주간 한도', 38,
                        reset if same_period else reset - timedelta(days=7), 604800)])
    partial = Snapshot(datetime.now(UTC), [QuotaLimit('weekly', '주간 한도', None, reset, 604800)])
    pane.snapshots['grokbot'] = previous
    monkeypatch.setattr(session, 'fetch', lambda _: partial)
    pane.policies['grokbot'].reconnect()
    try:
        pane.refresh('grokbot')
        pump(app, pane)
        assert pane.snapshots['grokbot'] is (previous if same_period else partial)
        assert pane.errors['grokbot'] == 'USAGE_UNAVAILABLE'
        assert not pane.policies['grokbot'].halted
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


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
        assert '인증 연결됨' in pane.cards['grok'][0].text()
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


@pytest.mark.parametrize('error_code', ['AUTH_REQUIRED', 'STORAGE_ERROR', 'REQUEST_REJECTED'])
def test_grok_cli_relogin_automatically_resumes_halted_controller(tmp_path, monkeypatch, error_code):
    from usagedesk.grok_auth import GrokAuthStore

    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions['grok']
    session.auth_store = GrokAuthStore(tmp_path / 'fake-auth.json')
    session.auth_store.path.write_text('{}', 'utf-8')
    session.tokens = True
    pane.policies['grok'].reconnect()
    calls = []
    def fail(_):
        calls.append('expired')
        raise ProviderError(error_code)
    monkeypatch.setattr(session, 'fetch', fail)
    try:
        pane.refresh('grok')
        pump(app, pane)
        assert pane.policies['grok'].halted
        pane.tick()
        assert calls == ['expired'] and not pane.tasks
        snapshot = Snapshot(datetime.now(UTC), [QuotaLimit('account', '주간 한도', 5)])
        def renewed(_):
            calls.append('renewed')
            return snapshot
        monkeypatch.setattr(session, 'fetch', renewed)
        session.auth_store.path.write_text('{"fake-cli-sign-in": true}', 'utf-8')
        pane.tick()
        pump(app, pane)
        assert calls == ['expired', 'renewed']
        assert 'grok' not in pane.errors and not pane.policies['grok'].halted
        assert pane.snapshots['grok'] is snapshot
        pane.disconnect('grok')
        session.auth_store.path.write_text('{}', 'utf-8')
        pane.tick()
        assert not pane.tasks  # Explicitly disconnected accounts stay disconnected.
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('previous_period', ['same', 'old', 'none'])
def test_grok_partial_billing_preserves_same_period_value_and_renders_reset(tmp_path, monkeypatch, previous_period):
    from datetime import timedelta

    from PySide6.QtWidgets import QLabel, QProgressBar

    from usagedesk.grok_auth import GrokAuthStore

    app = QApplication.instance() or QApplication([])
    pane = UsagePane(tmp_path, autoload=False)
    session = pane.sessions['grok']
    session.tokens = True
    session.auth_store = GrokAuthStore(tmp_path / 'fake-auth.json')
    pane.policies['grok'].reconnect()
    reset = datetime.now(UTC) + timedelta(days=2)
    if previous_period != 'none':
        before = Snapshot(datetime.now(UTC), [QuotaLimit('account', '주간 한도', 73,
                          reset if previous_period == 'same' else reset - timedelta(days=7), 604800)])
        pane.snapshots['grok'] = before
    partial = Snapshot(datetime.now(UTC), [QuotaLimit('account', '주간 한도', None, reset, 604800)])
    monkeypatch.setattr(session, 'fetch', lambda _: partial)
    try:
        pane.refresh('grok')
        pump(app, pane)
        assert pane.errors['grok'] == 'USAGE_UNAVAILABLE'
        assert not pane.policies['grok'].halted
        assert '인증 연결됨' in pane.cards['grok'][0].text()
        if previous_period == 'same':
            assert pane.snapshots['grok'] is before
            assert '이전 데이터' in pane.cards['grok'][0].text()
        else:
            assert pane.snapshots['grok'] is partial
            assert '마지막 인증·기간 조회' in pane.cards['grok'][0].text()
            layout = pane.cards['grok'][1]
            widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
            assert not any(isinstance(item, QProgressBar) for item in widgets)
            labels = [item.text() for item in widgets if isinstance(item, QLabel)]
            assert any('초기화' in text for text in labels)
            assert not any('0%' in text for text in labels)
        pane.policies['grok'].last_start -= 11
        current = Snapshot(datetime.now(UTC), [QuotaLimit('account', '주간 한도', 12, reset, 604800)])
        monkeypatch.setattr(session, 'fetch', lambda _: current)
        pane.refresh('grok', True)
        pump(app, pane)
        assert pane.snapshots['grok'] is current and 'grok' not in pane.errors
    finally:
        pane.shutdown()
        pane.pool.waitForDone()
        pane.deleteLater()
        app.processEvents()
