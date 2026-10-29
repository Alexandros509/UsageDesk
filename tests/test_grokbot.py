import base64
import json
import sqlite3
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from usagedesk.credentials import CredentialVault
from usagedesk.grokbot import (
    COOKIE_NAME,
    USAGE_URL,
    GrokBotSession,
    normalize_grokbot,
    read_cursor_session,
    session_cookie,
)
from usagedesk.oauth import ProviderError
from usagedesk.providers import Transport


def jwt(subject='auth0|test-user'):
    claims = base64.urlsafe_b64encode(json.dumps({'sub': subject}).encode()).decode().rstrip('=')
    return f'eyJhbGciOiJIUzI1NiJ9.{claims}.test_signature'


def response(**overrides):
    return dict({'usagePercent': 38, 'nextResetTimestampUtc': '2026-11-03T12:00:00Z',
                 'grokPlanLabel': 'SuperGrok', 'hasAvailableUsage': True,
                 'hasNonZeroIncludedLimit': True}, **overrides)


def database(path, token):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS ItemTable (key TEXT PRIMARY KEY, value TEXT)')
        db.execute('INSERT OR REPLACE INTO ItemTable VALUES (?, ?)',
                   ('cursorAuth/accessToken', token))
        db.execute('INSERT OR REPLACE INTO ItemTable VALUES (?, ?)',
                   ('unrelated-private-item', 'must-not-be-copied'))


@pytest.mark.parametrize('form', ['jwt', 'encoded', 'decoded', 'named'])
def test_cookie_normalization(form):
    token = jwt()
    cookie = 'test-user%3A%3A' + token
    raw = {'jwt': token, 'encoded': cookie, 'decoded': 'test-user::' + token,
           'named': COOKIE_NAME + '=' + cookie}[form]
    assert session_cookie(raw) == cookie


@pytest.mark.parametrize('raw', [None, '', 'not-a-token', 'a' * 16385, 'x%3A%3Aa.b.c;other=bad',
                                'x%3A%3Aa.b.c\r\nInjected: secret', 'x%3A%3Aa b.c.d',
                                'bad/subject%3A%3Aa.b.c', 'Cookie: x%3A%3Aa.b.c',
                                'x%3A%3Aa.b.c%3A%3Abad'])
def test_cookie_rejects_malformed_or_header_injection_without_echo(raw):
    with pytest.raises(ProviderError, match='^INVALID_SESSION$'):
        session_cookie(raw)


def test_cursor_storage_is_read_only_and_not_copied(tmp_path):
    path = tmp_path / 'state.vscdb'
    database(path, jwt())
    before = path.read_bytes()
    assert read_cursor_session(path) == 'test-user%3A%3A' + jwt()
    assert path.read_bytes() == before
    assert {p.name for p in tmp_path.iterdir()} == {'state.vscdb'}


def test_missing_local_credential_has_actionable_fixed_error(tmp_path):
    with pytest.raises(ProviderError, match='LOCAL_AUTH_MISSING'):
        read_cursor_session(tmp_path / 'missing')
    path = tmp_path / 'state.vscdb'
    database(path, '')
    with pytest.raises(ProviderError, match='LOCAL_AUTH_MISSING'):
        read_cursor_session(path)


def test_real_zero_unknown_and_shared_pool_are_distinct():
    snapshot = normalize_grokbot(response(isUnified=True))
    quota = snapshot.limits[0]
    assert quota.key == 'weekly' and quota.used_percent == 38 and quota.remaining_percent == 62
    assert quota.window_seconds == 604800
    assert quota.reset_at_utc == datetime(2026, 11, 3, 12, tzinfo=UTC)
    assert quota.label == '통합 주간 한도'
    assert any('SuperGrok' in note for note in snapshot.notes)
    assert normalize_grokbot(response(usagePercent=0)).limits[0].used_percent == 0
    partial = normalize_grokbot(response(usagePercent=None))
    assert partial.limits[0].used_percent is None and partial.warnings
    empty = normalize_grokbot(response(hasAvailableUsage=False, hasNonZeroIncludedLimit=False))
    assert len(empty.warnings) == 2


@pytest.mark.parametrize('body', [None, [], {}, response(usagePercent='38'),
                                 response(usagePercent=True), response(usagePercent=-1),
                                 response(usagePercent=float('nan')),
                                 response(nextResetTimestampUtc='bad'),
                                 response(nextResetTimestampUtc='2026-11-03T12:00:00')])
def test_schema_changes_and_missing_values_do_not_become_zero(body):
    with pytest.raises(ProviderError):
        normalize_grokbot(body)


def test_manual_session_is_dpapi_encrypted_restored_and_deleted(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response())

    transport = Transport(lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    vault = CredentialVault(tmp_path)
    session = GrokBotSession(vault, transport)
    assert session.restore(0) is False
    snapshot = session.connect(0, 'session', jwt())
    assert snapshot.limits[0].used_percent == 38
    encrypted = (tmp_path / 'secrets' / 'grokbot.dpapi').read_bytes()
    assert jwt().encode() not in encrypted
    request = requests[0]
    assert str(request.url) == USAGE_URL and request.method == 'POST'
    assert json.loads(request.content) == {}
    assert request.headers['Cookie'] == COOKIE_NAME + '=test-user%3A%3A' + jwt()
    assert request.headers['Origin'] == 'https://cursor.com'
    restored = GrokBotSession(vault, transport)
    assert restored.restore(0) is True
    assert restored.fetch(0).limits[0].used_percent == 38
    restored.reset(delete=True)
    assert not (tmp_path / 'secrets' / 'grokbot.dpapi').exists()
    with pytest.raises(ProviderError, match='AUTH_REQUIRED'):
        restored.fetch(restored.epoch)


def test_local_connection_stores_only_preference_and_rereads_login(tmp_path):
    path = tmp_path / 'state.vscdb'
    database(path, jwt())
    requests = []

    def handler(request):
        requests.append(request.headers['cookie'])
        return httpx.Response(200, json=response())

    transport = Transport(lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    vault = CredentialVault(tmp_path)
    session = GrokBotSession(vault, transport, path)
    session.connect(0, 'cursor')
    assert vault.load('grokbot') == {'source': 'cursor'}
    database(path, jwt('auth0|new-login'))
    session.fetch(0)
    assert requests[0] != requests[1] and 'new-login%3A%3A' in requests[1]
    original = path.read_bytes()
    session.reset(delete=True)
    assert path.read_bytes() == original


@pytest.mark.parametrize('status,code', [(401, 'AUTH_REQUIRED'), (403, 'BLOCKED'),
                                       (429, 'RATE_LIMITED'), (503, 'SERVER_ERROR'),
                                       (302, 'BLOCKED')])
def test_http_failures_preserve_encrypted_session_and_do_not_follow_redirect(tmp_path, status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={}, headers={'Retry-After': '75',
                                                       'Location': 'https://example.com/private'})

    transport = Transport(lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    session = GrokBotSession(CredentialVault(tmp_path), transport)
    with pytest.raises(ProviderError, match=code) as error:
        session.connect(0, 'session', jwt())
    assert len(requests) == 1 and str(requests[0].url) == USAGE_URL
    assert session.tokens is True
    assert session.vault.load('grokbot')['session_cookie'] == 'test-user%3A%3A' + jwt()
    if status == 429:
        assert error.value.retry_after == 75


def test_disconnect_during_request_discards_stale_result(tmp_path):
    session = None

    def handler(request):
        session.reset(delete=True)
        return httpx.Response(200, json=response())

    transport = Transport(lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw))
    session = GrokBotSession(CredentialVault(tmp_path), transport)
    with pytest.raises(ProviderError, match='CANCELLED'):
        session.connect(0, 'session', jwt())
    assert session.tokens is None and session.vault.load('grokbot') is None


def test_real_http_serialization_to_loopback_with_only_synthetic_auth(tmp_path):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            observed.append((self.path, self.headers['Cookie'],
                             self.rfile.read(int(self.headers['Content-Length']))))
            content = json.dumps(response()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def local_only(request):
        assert str(request.url) == USAGE_URL
        request.url = httpx.URL(f'http://127.0.0.1:{server.server_port}{request.url.path}')

    try:
        transport = Transport(lambda **kw: httpx.Client(event_hooks={'request': [local_only]}, **kw))
        session = GrokBotSession(CredentialVault(tmp_path), transport)
        assert session.connect(0, 'session', jwt()).limits[0].remaining_percent == 62
        assert observed == [('/api/dashboard/get-sand-usage-status',
                             COOKIE_NAME + '=test-user%3A%3A' + jwt(), b'{}')]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
