import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from usagedesk.grok import AUTH_SCOPE, BILLING_URL, TOKEN_URL, GrokSession, normalize_grok
from usagedesk.grok_auth import GrokAuthStore
from usagedesk.oauth import ProviderError
from usagedesk.providers import Transport


def quota(percent=37):
    return {"config": {"creditUsagePercent": percent, "isUnifiedBillingUser": True,
                       "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY",
                                         "end": "2026-10-08T00:10:06Z"}}}


def test_weekly_quota_is_account_percent_not_session_tokens():
    snapshot = normalize_grok(quota(100))
    limit, = snapshot.limits
    assert limit.used_percent == 100
    assert limit.remaining_percent == 0
    assert limit.window_seconds == 604800
    assert limit.reset_at_utc == datetime(2026, 10, 8, 0, 10, 6, tzinfo=UTC)
    assert "통합" in limit.label
    assert "공유" in snapshot.notes[0]
    assert normalize_grok(quota(0)).limits[0].used_percent == 0


@pytest.mark.parametrize("body", [{}, {"config": None}, {"config": {}},
                                  {"config": {"creditUsagePercent": None}},
                                  {"config": {"monthlyLimit": {}, "used": {}}}])
def test_missing_quota_never_becomes_zero(body):
    with pytest.raises(ProviderError, match="UNSUPPORTED" if (body.get("config") or {}).get("monthlyLimit") == {} else "USAGE_UNAVAILABLE"):
        normalize_grok(body)


@pytest.mark.parametrize("value", [True, "42", -1, float("inf"), float("nan")])
def test_malformed_percent_rejected(value):
    with pytest.raises(ProviderError, match="PARSE_ERROR"):
        normalize_grok(quota(value))


def test_legacy_cents_and_invalid_reset():
    snapshot = normalize_grok({"config": {"monthlyLimit": {"val": "2000"},
                                         "used": {"val": "500"}}})
    assert snapshot.limits[0].used_percent == 25
    assert snapshot.limits[0].window_seconds is None  # A month is not 30 days by assumption.
    bad = quota()
    bad["config"]["currentPeriod"]["end"] = "2026-10-08"
    snapshot = normalize_grok(bad)
    assert snapshot.limits[0].reset_at_utc is None
    assert snapshot.warnings


class Vault:
    def __init__(self):
        self.data = {}

    def save(self, key, value):
        self.data[key] = value

    def load(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


def auth_file(path, key="fake-access", **extra):
    auth = {"key": key, "user_id": "fake-user", "principal_type": "User",
            "team_id": "personal-account-team", "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": AUTH_SCOPE.split("::")[1],
            "refresh_token": "never-copy-this", **extra}
    path.write_text(json.dumps({AUTH_SCOPE: auth}), encoding="utf-8")


def test_link_rereads_cli_token_persists_only_preference_and_does_not_logout_cli(tmp_path):
    auth = tmp_path / "auth.json"
    auth_file(auth)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=quota())

    vault = Vault()
    session = GrokSession(vault, auth, Transport(
        lambda **kw: httpx.Client(transport=httpx.MockTransport(respond), **kw)))
    assert not session.restore(0)
    assert not requests
    assert session.connect(0).limits[0].used_percent == 37
    auth_file(auth, "updated-cli-access")
    session.fetch(0)
    assert str(requests[0].url) == BILLING_URL
    assert requests[1].headers["Authorization"] == "Bearer updated-cli-access"
    assert requests[1].headers["X-XAI-Token-Auth"] == "xai-grok-cli"
    assert vault.data == {"grok": {"cli_link": True}}
    assert session.tokens is True
    contents = auth.read_bytes()
    session.reset(delete=True)
    assert not vault.data and auth.read_bytes() == contents
    with pytest.raises(ProviderError, match="CANCELLED"):
        session.fetch(0)


def test_expired_auth_missing_auth_and_foreign_issuer_never_send(tmp_path):
    auth = tmp_path / "auth.json"
    session = GrokSession(Vault(), auth)
    with pytest.raises(ProviderError, match="AUTH_REQUIRED"):
        session.credential()
    auth_file(auth, refresh_token=None,
              expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    with pytest.raises(ProviderError, match="AUTH_REQUIRED"):
        session.credential()
    auth_file(auth, oidc_issuer="https://example.com")
    with pytest.raises(ProviderError, match="AUTH_REQUIRED"):
        session.credential()


@pytest.mark.parametrize("status,code", [(401, "AUTH_REQUIRED"), (429, "RATE_LIMITED"),
                                       (302, "BLOCKED")])
def test_grok_transport_does_not_follow_redirects_or_hide_errors(tmp_path, status, code):
    auth = tmp_path / "auth.json"
    auth_file(auth, refresh_token=None)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.com", "Retry-After": "90"})

    session = GrokSession(Vault(), auth, Transport(
        lambda **kw: httpx.Client(transport=httpx.MockTransport(respond), **kw)))
    with pytest.raises(ProviderError, match=code):
        session.connect(0)
    assert len(calls) == 1


def test_disconnect_during_request_discards_late_success(tmp_path):
    auth = tmp_path / "auth.json"
    auth_file(auth)

    def respond(request):
        session.reset(delete=True)
        return httpx.Response(200, json=quota())

    session = GrokSession(Vault(), auth, Transport(
        lambda **kw: httpx.Client(transport=httpx.MockTransport(respond), **kw)))
    with pytest.raises(ProviderError, match="CANCELLED"):
        session.connect(0)


def transport_for(respond):
    return Transport(lambda **kw: httpx.Client(transport=httpx.MockTransport(respond), **kw))


def rotated():
    return {'access_token': 'fake-renewed', 'refresh_token': 'fake-rotated', 'expires_in': 3600}


@pytest.mark.parametrize('seconds', [-10, 90])
def test_automatic_refresh_preserves_cli_identity_other_scopes_and_private_permissions(tmp_path, seconds):
    import win32security

    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) + timedelta(seconds=seconds)).isoformat())
    original = json.loads(auth.read_text('utf-8'))
    original['another-cli-account'] = {'key': 'another-fake-secret'}
    auth.write_text(json.dumps(original), 'utf-8')
    calls = []
    def respond(request):
        calls.append(request)
        if request.method == 'POST':
            assert str(request.url) == TOKEN_URL
            assert b'grant_type=refresh_token' in request.content
            assert b'refresh_token=never-copy-this' in request.content
            return httpx.Response(200, json=rotated())
        assert request.headers['Authorization'] == 'Bearer fake-renewed'
        assert request.headers['x-userid'] == 'fake-user'
        return httpx.Response(200, json=quota())
    vault = Vault()
    session = GrokSession(vault, auth, transport_for(respond))
    assert session.connect(0).limits[0].used_percent == 37
    assert [r.method for r in calls] == ['POST', 'GET']
    saved = json.loads(auth.read_text('utf-8'))
    assert saved['another-cli-account'] == original['another-cli-account']
    assert saved[AUTH_SCOPE]['key'] == 'fake-renewed'
    assert saved[AUTH_SCOPE]['refresh_token'] == 'fake-rotated'
    assert saved[AUTH_SCOPE]['user_id'] == 'fake-user'
    assert saved[AUTH_SCOPE]['team_id'] == 'personal-account-team'
    assert datetime.fromisoformat(saved[AUTH_SCOPE]['expires_at']) > datetime.now(UTC)
    assert vault.data == {'grok': {'cli_link': True}}
    assert not list(tmp_path.glob('auth-*.tmp'))
    acl = win32security.GetNamedSecurityInfo(str(auth), win32security.SE_FILE_OBJECT,
                                           win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    assert acl.GetAceCount() == 2
    session.fetch(0)
    assert [r.method for r in calls] == ['POST', 'GET', 'GET']


@pytest.mark.parametrize('second_status', [200, 401])
def test_billing_401_refreshes_once_then_retries_once(tmp_path, second_status):
    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) + timedelta(hours=2)).isoformat())
    methods = []
    def respond(request):
        methods.append(request.method)
        if request.method == 'POST':
            return httpx.Response(200, json=rotated())
        status = 401 if len(methods) == 1 else second_status
        return httpx.Response(status, json=quota() if status == 200 else {})
    session = GrokSession(Vault(), auth, transport_for(respond))
    if second_status == 200:
        assert session.connect(0).limits[0].used_percent == 37
    else:
        with pytest.raises(ProviderError, match='AUTH_REQUIRED'):
            session.connect(0)
    assert methods == ['GET', 'POST', 'GET']


@pytest.mark.parametrize('status,code', [(400, 'AUTH_REQUIRED'), (429, 'RATE_LIMITED'),
                                       (503, 'SERVER_ERROR'), (302, 'BLOCKED')])
def test_refresh_errors_keep_credentials_and_do_not_retry_inline(tmp_path, status, code):
    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    before = auth.read_bytes()
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == 'POST'
        return httpx.Response(status, json={'error': 'invalid_grant'}, headers={
            'Retry-After': '90', 'Location': 'https://example.com'})
    session = GrokSession(Vault(), auth, transport_for(respond))
    with pytest.raises(ProviderError, match=code):
        session.connect(0)
    assert len(calls) == 1 and auth.read_bytes() == before


def test_disconnect_during_refresh_persists_rotation_but_discards_usage(tmp_path):
    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    calls = []
    def respond(request):
        calls.append(request.method)
        assert request.method == 'POST'
        session.reset(delete=True)
        return httpx.Response(200, json=rotated())
    vault = Vault()
    session = GrokSession(vault, auth, transport_for(respond))
    with pytest.raises(ProviderError, match='CANCELLED'):
        session.connect(0)
    assert calls == ['POST'] and vault.data == {}
    assert json.loads(auth.read_text('utf-8'))[AUTH_SCOPE]['refresh_token'] == 'fake-rotated'


def test_failed_disk_write_retains_rotation_and_retry_does_not_reuse_old_refresh(tmp_path, monkeypatch):
    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    before = auth.read_bytes()
    methods = []
    def respond(request):
        methods.append(request.method)
        return httpx.Response(200, json=rotated() if request.method == 'POST' else quota())
    session = GrokSession(Vault(), auth, transport_for(respond))
    original = session.auth_store.replace_auth
    def denied(*args):
        raise ProviderError('STORAGE_ERROR')
    monkeypatch.setattr(session.auth_store, 'replace_auth', denied)
    with pytest.raises(ProviderError, match='STORAGE_ERROR'):
        session.connect(0)
    assert auth.read_bytes() == before
    monkeypatch.setattr(session.auth_store, 'replace_auth', original)
    assert session.fetch(0).limits[0].used_percent == 37
    assert methods == ['POST', 'GET']
    assert session.pending_auth is None


def test_native_shared_lock_serializes_two_sessions_and_rereads_fresh_token(tmp_path):
    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    methods = []
    def respond(request):
        methods.append(request.method)
        if request.method == 'POST':
            time.sleep(.1)  # Make a second native lock contender observable.
        return httpx.Response(200, json=rotated() if request.method == 'POST' else quota())
    sessions = [GrokSession(Vault(), auth, transport_for(respond)) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda session: session.connect(0), sessions))
    assert all(s.limits[0].used_percent == 37 for s in results)
    assert methods.count('POST') == 1 and methods.count('GET') == 2


def test_native_cli_lock_timeout_cancel_and_release_do_not_delete_lock(tmp_path):
    auth = tmp_path / 'auth.json'
    auth_file(auth)
    store = GrokAuthStore(auth)
    cancel = threading.Event()
    with store.refresh_lock(cancel):
        with pytest.raises(ProviderError, match='AUTH_BUSY'), store.refresh_lock(cancel, timeout=.1):
            pytest.fail('Held lock was stolen')
        stopped = threading.Event()
        stopped.set()
        with pytest.raises(ProviderError, match='CANCELLED'), store.refresh_lock(stopped):
            pytest.fail('Canceled waiter acquired lock')
    assert auth.with_name('auth.json.lock').exists()
    with store.refresh_lock(cancel):
        pass


def test_changed_cli_login_during_refresh_is_not_overwritten(tmp_path):
    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    methods = []
    def respond(request):
        methods.append(request.method)
        if request.method == 'POST':
            auth_file(auth, key='new-cli-login', expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
            return httpx.Response(200, json=rotated())
        assert request.headers['Authorization'] == 'Bearer new-cli-login'
        return httpx.Response(200, json=quota())
    session = GrokSession(Vault(), auth, transport_for(respond))
    with pytest.raises(ProviderError, match='STORAGE_ERROR'):
        session.connect(0)
    assert json.loads(auth.read_text('utf-8'))[AUTH_SCOPE]['key'] == 'new-cli-login'
    assert session.fetch(0).limits[0].used_percent == 37
    assert methods == ['POST', 'GET']


def test_atomic_replace_failure_keeps_cli_auth_and_removes_private_temp(tmp_path, monkeypatch):
    auth = tmp_path / 'auth.json'
    auth_file(auth)
    before = auth.read_bytes()
    expected = json.loads(before)[AUTH_SCOPE]
    store = GrokAuthStore(auth)
    def denied(*args):
        raise PermissionError('simulated replacement failure')
    monkeypatch.setattr('usagedesk.grok_auth.os.replace', denied)
    with store.refresh_lock(threading.Event()):
        with pytest.raises(ProviderError, match='STORAGE_ERROR'):
            store.replace_auth(AUTH_SCOPE, expected, dict(expected, key='fake-new'))
    assert auth.read_bytes() == before
    assert not list(tmp_path.glob('auth-*.tmp'))


def test_refresh_network_failure_keeps_credentials_for_retry(tmp_path):
    auth = tmp_path / 'auth.json'
    auth_file(auth, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    before = auth.read_bytes()
    def offline(request):
        raise httpx.ConnectError('simulated offline')
    session = GrokSession(Vault(), auth, transport_for(offline))
    with pytest.raises(ProviderError, match='OFFLINE'):
        session.connect(0)
    assert session.tokens is True
    assert auth.read_bytes() == before
