from datetime import UTC, datetime, timedelta, timezone

import pytest

from usagedesk.domain import QuotaLimit, RefreshPolicy, retry_after_seconds


@pytest.mark.parametrize("value", [True, False, -1, float("nan"), float("inf"), "10"])
def test_invalid_usage_never_becomes_zero(value):
    with pytest.raises(ValueError):
        QuotaLimit("short", "짧은 창", value)


def test_unknown_zero_and_over_limit_are_distinct():
    assert QuotaLimit("a", "A", None).remaining_percent is None
    assert QuotaLimit("a", "A", 0).remaining_percent == 100
    quota = QuotaLimit("a", "A", 125.5)
    assert quota.used_percent == 125.5
    assert quota.remaining_percent == 0


def test_reset_timezone_required_and_normalized():
    with pytest.raises(ValueError):
        QuotaLimit("a", "A", 10, datetime(2026, 10, 5))
    quota = QuotaLimit("a", "A", 10, datetime(2026, 10, 5, 9, tzinfo=timezone(timedelta(hours=9))))
    assert quota.reset_at_utc == datetime(2026, 10, 5, tzinfo=UTC)


def test_single_flight_manual_cooldown_and_regular_interval():
    policy = RefreshPolicy()
    assert policy.begin(0) is None  # Unconfigured.
    policy.reconnect()
    token = policy.begin(0)
    assert token is not None
    assert policy.begin(1, manual=True) is None
    assert policy.finish(token, 2)
    assert policy.begin(9, manual=True) is None
    assert policy.begin(100) is None
    assert policy.begin(10, manual=True) is not None


def test_429_backoff_cannot_be_bypassed_and_success_resets():
    policy = RefreshPolicy()
    policy.reconnect()
    token = policy.begin(0)
    policy.finish(token, 1, "RATE_LIMITED", retry_after=3600, jitter=2)
    assert policy.begin(3000, manual=True) is None
    token = policy.begin(3603, manual=True)
    assert token is not None
    policy.finish(token, 3604)
    assert policy.failures == 0
    assert policy.next_auto == 3784


def test_backoff_sequence_and_generation_reject_old_completion():
    policy = RefreshPolicy()
    policy.reconnect()
    now = 0
    for delay in [60, 120, 240, 480, 900, 900]:
        token = policy.begin(now)
        policy.finish(token, now + 1, "OFFLINE")
        assert policy.retry_at == now + 1 + delay
        now = policy.retry_at
    old = policy.begin(now)
    policy.disconnect()
    assert not policy.finish(old, now + 1)
    policy.reconnect()
    current = policy.begin(now + 2)
    assert not policy.finish(old, now + 3)
    assert policy.inflight
    assert policy.finish(current, now + 4)


@pytest.mark.parametrize("error", ["AUTH_REQUIRED", "BLOCKED"])
def test_auth_and_security_errors_stop_automatic_polling(error):
    policy = RefreshPolicy()
    policy.reconnect()
    token = policy.begin(0)
    policy.finish(token, 1, error)
    assert policy.begin(10000, manual=True) is None


def test_retry_after_http_date_delta_and_invalid():
    now = datetime(2026, 10, 5, tzinfo=UTC)
    assert retry_after_seconds("120", now) == 120
    assert retry_after_seconds("Mon, 05 Oct 2026 01:00:00 GMT", now) == 3600
    assert retry_after_seconds("Sun, 04 Oct 2026 23:00:00 GMT", now) == 0
    for value in [None, "garbage", "-1", "nan", "inf", "1.2"]:
        assert retry_after_seconds(value, now) is None
