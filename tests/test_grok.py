import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from usagedesk.grok import AUTH_SCOPE, BILLING_URL, GrokSession, normalize_grok
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
    with pytest.raises(ProviderError, match="UNSUPPORTED"):
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
    auth_file(auth, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    with pytest.raises(ProviderError, match="AUTH_REQUIRED"):
        session.credential()
    auth_file(auth, oidc_issuer="https://example.com")
    with pytest.raises(ProviderError, match="AUTH_REQUIRED"):
        session.credential()


@pytest.mark.parametrize("status,code", [(401, "AUTH_REQUIRED"), (429, "RATE_LIMITED"),
                                       (302, "BLOCKED")])
def test_grok_transport_does_not_follow_redirects_or_hide_errors(tmp_path, status, code):
    auth = tmp_path / "auth.json"
    auth_file(auth)
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
