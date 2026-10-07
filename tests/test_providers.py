import json
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime

import httpx
import pytest

from usagedesk.oauth import CONFIGS, ProviderError, Transaction
from usagedesk.providers import AccountSession, Provider, Tokens, Transport, normalize_usage


def test_claude_schema_variants_missing_and_partial_errors():
    snap = normalize_usage(
        "claude",
        {
            "five_hour": {"utilization": 0, "resets_at": None},
            "seven_day": None,
            "seven_day_opus": {"utilization": -2},
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "percent": 125.5,
                    "scope": {"model": {"id": "example", "display_name": "Model"}},
                }
            ],
        },
    )
    assert [q.used_percent for q in snap.limits] == [0, 125.5]
    assert "seven_day" not in [q.key for q in snap.limits]
    assert snap.warnings


def test_codex_weekly_primary_relative_reset_and_credits():
    now = datetime(2026, 10, 6, tzinfo=UTC)
    snap = normalize_usage(
        "codex",
        {
            "rate_limit": {
                "primary_window": {
                    "used_percent": 0,
                    "limit_window_seconds": 604800,
                    "reset_after_seconds": 60,
                }
            },
            "credits": {"balance": 12.5},
        },
        now,
    )
    quota = snap.limits[0]
    assert quota.label.startswith("7일")
    assert quota.window_seconds == 604800
    assert (quota.reset_at_utc - now).total_seconds() == 60
    assert snap.notes == ["남은 크레딧: 12.5"]


def test_claude_legacy_and_modern_account_windows_are_not_double_counted():
    snapshot = normalize_usage("claude", {
        "five_hour": {"utilization": 0}, "seven_day": {"utilization": 100},
        "limits": [
            {"kind": "session", "percent": 0},
            {"kind": "weekly_all", "percent": 100, "scope": {"model": None, "surface": None}},
            {"kind": "session", "percent": 0},
        ],
    })
    assert [(q.key, q.used_percent) for q in snapshot.limits] == [("five_hour", 0), ("seven_day", 100)]
    assert snapshot.aliases == {"limit:0": "five_hour", "limit:1": "seven_day", "limit:2": "five_hour"}


def test_claude_identical_values_with_distinct_model_or_surface_are_preserved():
    snapshot = normalize_usage("claude", {
        "seven_day": {"utilization": 50},
        "limits": [
            {"kind": "weekly_scoped", "percent": 50,
             "scope": {"model": {"id": "sonnet", "display_name": "Sonnet"}}},
            {"kind": "weekly_all", "percent": 50, "scope": {"surface": "oauth_apps"}},
        ],
    })
    assert len(snapshot.limits) == 3
    assert len({q.key for q in snapshot.limits}) == 3


def test_claude_invalid_legacy_falls_back_to_valid_modern_window():
    snapshot = normalize_usage("claude", {
        "five_hour": {"utilization": -1},
        "limits": [{"kind": "session", "percent": 23}],
    })
    assert [(q.key, q.used_percent) for q in snapshot.limits] == [("five_hour", 23)]
    assert snapshot.warnings


def test_claude_conflicting_aliases_warn_instead_of_silently_overwriting():
    snapshot = normalize_usage("claude", {
        "five_hour": {"utilization": 10},
        "limits": [{"kind": "session", "percent": 12}],
    })
    assert len(snapshot.limits) == 1
    assert snapshot.limits[0].used_percent == 10
    assert any("동일 한도" in warning for warning in snapshot.warnings)


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_empty_response_does_not_fabricate_zero(provider):
    with pytest.raises(ProviderError, match="UNSUPPORTED"):
        normalize_usage(provider, {})


def test_unknown_window_not_labeled_five_hours():
    snap = normalize_usage("codex", {"rate_limit": {"primary_window": {"used_percent": 25}}})
    assert snap.limits[0].label == "주 제한"
    assert snap.limits[0].reset_at_utc is None


def transport(handler):
    return Transport(
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "AUTH_REQUIRED"),
        (403, "BLOCKED"),
        (429, "RATE_LIMITED"),
        (500, "SERVER_ERROR"),
        (302, "BLOCKED"),
    ],
)
def test_http_errors_are_sanitized(status, expected):
    http = transport(
        lambda _: httpx.Response(
            status,
            json={"error": "secret-token"},
            headers={"Retry-After": "1800", "Location": "https://untrusted.test"},
        )
    )
    with pytest.raises(ProviderError) as error:
        http.request("GET", CONFIGS["claude"].usage_url)
    assert error.value.code == expected
    assert "secret-token" not in str(error.value)
    if status == 429:
        assert error.value.retry_after == 1800


def test_exchange_content_types_and_state():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(
            200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}
        )

    for key in CONFIGS:
        tx = Transaction(CONFIGS[key], f"http://localhost:1234{CONFIGS[key].callback_path}")
        Provider(CONFIGS[key], transport(handler)).exchange(tx, "code")
    assert captured[0].headers["Content-Type"] == "application/json"
    assert json.loads(captured[0].content)["state"]
    assert captured[1].headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert b"code_verifier=" in captured[1].content


def test_invalid_grant_does_not_expose_body():
    http = transport(
        lambda _: httpx.Response(400, json={"error": "invalid_grant", "secret": "sentinel"})
    )
    with pytest.raises(ProviderError, match="AUTH_REQUIRED"):
        http.request("POST", CONFIGS["codex"].token_url)


def test_cancellation_prevents_any_http_request():
    event = threading.Event()
    event.set()
    http = transport(lambda _: pytest.fail("Should not send request"))
    with pytest.raises(ProviderError, match="CANCELLED"):
        http.request("GET", CONFIGS["claude"].usage_url, cancel=event)


class Vault:
    def __init__(self):
        self.data = {}

    def save(self, key, data):
        self.data[key] = data

    def load(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


def test_refresh_rotation_saved_even_when_usage_fails():
    vault = Vault()

    class Fake:
        def refresh(self, tokens, cancel):
            return Tokens("new-access", "new-refresh", time.time() + 3600)

        def usage(self, tokens, cancel):
            assert vault.data["claude"]["refresh_token"] == "new-refresh"
            raise ProviderError("OFFLINE")

    session = AccountSession("claude", vault, Fake())
    session.commit(0, Tokens("old-access", "old-refresh", 1))
    with pytest.raises(ProviderError, match="OFFLINE"):
        session.fetch(0)
    assert session.tokens.refresh_token == "new-refresh"


def test_logout_during_refresh_cannot_restore_credentials():
    vault = Vault()

    class Fake:
        def refresh(self, tokens, cancel):
            session.reset(delete=True)
            return Tokens("new-access", "new-refresh", time.time() + 3600)

    session = AccountSession("claude", vault, Fake())
    session.commit(0, Tokens("access", "refresh", 1))
    with pytest.raises(ProviderError, match="CANCELLED"):
        session.fetch(0)
    assert vault.data == {}
    assert session.tokens is None


def test_refresh_without_rotation_keeps_previous_refresh_token():
    old = Tokens("access", "keep-me")
    new = Tokens.parse({"access_token": "new"}, old)
    assert new.refresh_token == "keep-me"
    assert "keep-me" not in repr(new)
    assert Tokens.parse(asdict(new)).refresh_token == "keep-me"
