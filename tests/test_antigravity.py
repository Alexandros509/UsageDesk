"""Antigravity CLI transport, shared quota semantics and complete UI connection flow."""

import copy
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from usagedesk.antigravity import (
    OUTPUT_LIMIT,
    AntigravitySession,
    find_cli,
    normalize_antigravity,
    read_usage,
)
from usagedesk.bar import (
    DisplayDialog,
    DisplayOptions,
    display_percent,
    percentage_text,
    quota_caption,
    visible_limits,
)
from usagedesk.oauth import ProviderError
from usagedesk.usage_ui import AntigravityLoginDialog

NOW = datetime(2026, 11, 25, 0, 0, tzinfo=UTC)


def report():
    def bucket(key, window, remaining):
        return {'id': key, 'window': window, 'remaining_fraction': remaining,
                'reset_time': '2026-11-26T00:00:00Z'}
    return {'status': 'SUCCESS', 'command': {'name': 'usage', 'data': {'groups': [
        {'name': 'Gemini Models', 'buckets': [bucket('gemini-5h', '5h', 1),
                                             bucket('gemini-weekly', 'weekly', 0)]},
        {'name': 'Claude and GPT models', 'buckets': [bucket('partner-5h', '5h', .4),
                                                     bucket('partner-weekly', 'weekly', .8)]},
        {'name': 'Gemini 4 Argon', 'buckets': [bucket('argon-weekly', 'weekly', .25)]},
    ]}}}


class Vault:
    def __init__(self):
        self.values = {}

    def load(self, key):
        return self.values.get(key)

    def save(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def cli(tmp_path):
    path = tmp_path / 'agy.exe'
    path.write_bytes(b'not executed; process transport is injected in tests')
    return path


def test_dynamic_pools_do_not_invent_per_model_neon_quota():
    value = normalize_antigravity(report(), NOW)
    assert [q.label for q in value.limits] == ['Gemini · 5h', 'Gemini · 7d',
                                              'Claude/GPT · 5h', 'Claude/GPT · 7d', 'Argon · 7d']
    assert [q.used_percent for q in value.limits] == [0, 100, 60, pytest.approx(20), 75]
    assert all(q.reset_at_utc.utcoffset().total_seconds() == 0 for q in value.limits)
    assert not any('Neon' in q.label for q in value.limits)
    data = report()
    data['command']['data']['groups'].append({'name': 'Gemini 4 Neon', 'buckets': [
        {'id': 'neon-weekly', 'window': 'weekly', 'remaining_fraction': .6}]})
    assert normalize_antigravity(data).limits[-1].label == 'Neon · 7d'


def test_same_explicit_bucket_is_displayed_once_but_conflict_is_rejected():
    body = report()
    shared = copy.deepcopy(body['command']['data']['groups'][0])
    shared['name'] = 'Gemini 4 Neon'
    body['command']['data']['groups'].append(shared)
    assert len(normalize_antigravity(body).limits) == 5
    shared['buckets'][0]['remaining_fraction'] = .5
    with pytest.raises(ProviderError, match='PARSE_ERROR'):
        normalize_antigravity(body)


@pytest.mark.parametrize('remaining', [None, 0, 1])
def test_unknown_and_zero_remaining_are_distinct(remaining):
    body = report()
    body['command']['data']['groups'][0]['buckets'][0]['remaining_fraction'] = remaining
    q = normalize_antigravity(body).limits[0]
    assert q.used_percent == (None if remaining is None else (1 - remaining) * 100)


@pytest.mark.parametrize('remaining', [-.01, 1.01, True, '0.5', float('nan'), float('inf')])
def test_malformed_fraction_cannot_become_a_full_gauge(remaining):
    body = report()
    body['command']['data']['groups'][0]['buckets'][0]['remaining_fraction'] = remaining
    with pytest.raises(ProviderError, match='PARSE_ERROR'):
        normalize_antigravity(body)


@pytest.mark.parametrize('field,value', [('reset_time', '2026-11-25T03:00:00'),
    ('reset_time', 123), ('id', '../credentials'), ('id', ''), ('window', True)])
def test_malformed_identity_or_reset_rejects_the_snapshot(field, value):
    body = report()
    body['command']['data']['groups'][0]['buckets'][0][field] = value
    with pytest.raises(ProviderError, match='PARSE_ERROR'):
        normalize_antigravity(body)


def test_only_explicit_successful_usage_commands_are_accepted():
    for body in ({'status': 'SUCCESS', 'result': {'response': json.dumps(report())}},
                 {'status': 'SUCCESS', 'command': {'data': {'groups': None}}},
                 {'status': 'SUCCESS', 'command': {'name': 'model', 'data': report()['command']['data']}}):
        with pytest.raises(ProviderError, match='PARSE_ERROR'):
            normalize_antigravity(body)
    with pytest.raises(ProviderError, match='AUTH_REQUIRED'):
        normalize_antigravity({'status': 'ERROR', 'error': {'code': 'UNAUTHENTICATED'}})
    with pytest.raises(ProviderError, match='RATE_LIMITED'):
        normalize_antigravity({'status': 'ERROR', 'error': {'code': 'RESOURCE_EXHAUSTED'}})


def test_disabled_missing_and_new_window_semantics():
    body = report()
    bucket = body['command']['data']['groups'][0]['buckets'][0]
    bucket['enabled'] = False
    assert len(normalize_antigravity(body).limits) == 4
    bucket['enabled'] = True
    bucket['window'] = 'a-new-window'
    bucket.pop('reset_time')
    q = next(q for q in normalize_antigravity(body).limits if q.key == 'agy/gemini-5h')
    assert q.window_seconds is None and q.reset_at_utc is None
    body['command']['data']['groups'] = []
    with pytest.raises(ProviderError, match='USAGE_UNAVAILABLE'):
        normalize_antigravity(body)


def test_saved_quota_selection_survives_a_group_display_name_change():
    body = report()
    first = normalize_antigravity(body)
    body['command']['data']['groups'][0]['name'] = 'Gemini Updated Models'
    second = normalize_antigravity(body)
    assert [q.key for q in first.limits] == [q.key for q in second.limits]
    options = DisplayOptions(mode='custom', selected=('antigravity/agy/gemini-5h',))
    assert len(visible_limits(second, 'antigravity', options)) == 1


def test_live_style_weekly_first_output_is_sorted_and_percentage_is_compact():
    body = report()
    group = body['command']['data']['groups'][0]
    group['buckets'].reverse()
    group['buckets'][0]['remaining_fraction'] = .6566886901855469
    group['buckets'][1]['remaining_fraction'] = .7139828205108643
    value = normalize_antigravity(body)
    assert value.limits[0].window_seconds == 18000
    options = DisplayOptions(quota_display='remaining')
    assert percentage_text(value.limits[0], options, 'antigravity', value) == '71.4%'
    assert value.limits[0].remaining_percent == pytest.approx(71.39828205108643)


def test_weekly_exhaustion_only_affects_the_same_antigravity_pool():
    snapshot = normalize_antigravity(report(), NOW)
    options = replace(DisplayOptions(),
                      quota_display='remaining', reset_display='inline')
    gemini, _, partner = snapshot.limits[:3]
    assert display_percent(gemini, options, provider='antigravity', snapshot=snapshot) == 0
    assert display_percent(partner, options, provider='antigravity', snapshot=snapshot) == 40
    assert '주간 한도 소진' in quota_caption(gemini, options, NOW, provider='antigravity', snapshot=snapshot)
    assert quota_caption(partner, options, NOW) == '5h │ 초기화 1d 0hr'
    assert partner.label == 'Claude/GPT · 5h'  # Keep model identity in details and hover.
    unknown = replace(partner, used_percent=None)
    snapshot.limits.append(unknown)
    assert unknown in visible_limits(snapshot, 'antigravity', options)


def test_link_preference_restore_and_disconnect_never_store_tokens(tmp_path, cli):
    vault = Vault()
    def runner(*args):
        return report()
    session = AntigravitySession(vault, tmp_path, runner)
    assert not session.restore(session.epoch)
    session.connect(session.epoch, cli)
    assert vault.values == {'antigravity': {'cli_link': True, 'cli_path': str(cli)}}
    restored = AntigravitySession(vault, tmp_path, runner)
    assert restored.restore(restored.epoch)
    assert len(restored.fetch(restored.epoch).limits) == 5
    restored.reset(delete=True)
    assert not vault.values and cli.exists()
    with pytest.raises(ProviderError, match='AUTH_REQUIRED'):
        restored.fetch(restored.epoch)


def test_disconnect_during_lookup_discards_late_result(tmp_path, cli):
    vault = Vault()
    session = AntigravitySession(vault, tmp_path)
    session.runner = lambda *args: (session.reset(delete=True), report())[1]
    with pytest.raises(ProviderError, match='CANCELLED'):
        session.connect(session.epoch, cli)
    assert session.tokens is None and vault.values == {}


def test_failed_preference_write_does_not_create_a_connected_session(tmp_path, cli):
    vault = Vault()
    vault.save = lambda *args: (_ for _ in ()).throw(ProviderError('STORAGE_ERROR'))
    session = AntigravitySession(vault, tmp_path)
    with pytest.raises(ProviderError, match='STORAGE_ERROR'):
        session.connect(session.epoch, cli)
    assert session.tokens is None


def test_discovery_uses_official_windows_location_without_executing_it(cli, monkeypatch):
    monkeypatch.setenv('AGY_BIN', str(cli))
    assert find_cli() == cli
    monkeypatch.delenv('AGY_BIN')
    monkeypatch.setenv('LOCALAPPDATA', str(cli.parent))
    path = cli.parent / 'agy' / 'bin' / 'agy.exe'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'fake')
    assert find_cli() == path


def spawn_code(code, seen=None):
    def spawn(arguments, **kwargs):
        if seen is not None:
            seen.append((arguments, kwargs))
        return subprocess.Popen([sys.executable, '-c', code], **kwargs)
    return spawn


def test_actual_subprocess_command_is_only_usage_and_does_not_write_logs(tmp_path, cli):
    seen = []
    body = read_usage(cli, threading.Event(), tmp_path / 'empty-workspace',
                      popen=spawn_code(f'print({json.dumps(report())!r})', seen))
    assert normalize_antigravity(body).limits
    arguments, options = seen[0]
    assert arguments[1:5] == ['-p', '/usage', '--output-format', 'json']
    assert '--disable-slash-commands=false' in arguments and arguments[-1] == os.devnull
    assert options['stdin'] == subprocess.DEVNULL and options['stderr'] == subprocess.DEVNULL
    assert options['cwd'] == str(tmp_path / 'empty-workspace')


@pytest.mark.parametrize('kind,expected', [('timeout', 'TIMEOUT'), ('cancel', 'CANCELLED'),
    ('oversize', 'PARSE_ERROR'), ('invalid', 'PARSE_ERROR'), ('failed', 'AGY_COMMAND_FAILED')])
def test_subprocess_failure_and_cancellation_are_bounded(tmp_path, cli, kind, expected):
    cancel = threading.Event()
    codes = {'timeout': 'import time; time.sleep(10)', 'cancel': 'import time; time.sleep(10)',
             'oversize': f'import sys; sys.stdout.buffer.write(b"x" * {OUTPUT_LIMIT + 1})',
             'invalid': 'print("not JSON")', 'failed': 'raise SystemExit(1)'}
    timer = threading.Timer(.1, cancel.set) if kind == 'cancel' else None
    if timer:
        timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(ProviderError, match=expected):
            read_usage(cli, cancel, tmp_path / 'isolated', timeout=.3,
                       popen=spawn_code(codes[kind]))
        assert time.monotonic() - start < 4
    finally:
        if timer:
            timer.cancel()


def test_ui_connection_restore_visibility_and_selection(tmp_path, cli, monkeypatch):
    from usagedesk.storage import ConfigStore
    from usagedesk.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(ConfigStore(tmp_path), tray_enabled=False)
    window.timer.stop()
    pane = window.usage
    pane.timer.stop()
    pane.pool.waitForDone()
    app.processEvents()
    session = pane.sessions['antigravity']
    session.vault = Vault()
    session.runner = lambda *args: report()
    window.bar.available_geometry = lambda: QRect(0, 0, 1920, 1080)
    monkeypatch.setattr(AntigravityLoginDialog, 'exec', lambda dialog: (
        setattr(dialog, 'cli_path', cli), QDialog.Accepted)[1])
    try:
        window.bar.refresh()
        assert not [i for i in window.bar.items if i[4] == 'antigravity']
        pane.login('antigravity')
        end = time.monotonic() + 3
        while pane.tasks and time.monotonic() < end:
            app.processEvents()
            QTest.qWait(10)
        assert not pane.tasks and len(pane.snapshots['antigravity'].limits) == 5
        window.bar.refresh()
        assert any(i[4] == 'antigravity' for i in window.bar.items)
        dialog = DisplayDialog(window, window.bar.options, pane.snapshots)
        assert any(k.startswith('antigravity/') for k in dialog.checks)
        dialog.deleteLater()
        pane.disconnect('antigravity')
        window.bar.refresh()
        assert not [i for i in window.bar.items if i[4] == 'antigravity']
        assert session.vault.values == {} and cli.exists()
    finally:
        window.quitting = True
        pane.shutdown()
        pane.pool.waitForDone()
        window.hotkeys.close()
        window.bar.dock.remove()
        window.bar.hide()
        window.deleteLater()
        app.processEvents()


def test_connection_dialog_requires_real_executable_path(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = AntigravityLoginDialog()
    try:
        dialog.path.setText(str(tmp_path / 'missing.exe'))
        dialog.accept()
        assert dialog.result() != QDialog.Accepted
        assert '찾지 못했습니다' in dialog.status.text()
    finally:
        dialog.deleteLater()
        app.processEvents()
