from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .i18n import tr


@dataclass(frozen=True)
class QuotaLimit:
    key: str
    label: str
    used_percent: float | None
    reset_at_utc: datetime | None = None
    window_seconds: int | None = None

    def __post_init__(self) -> None:
        value = self.used_percent
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
        ):
            raise ValueError(tr('사용률은 0 이상의 유한 숫자여야 합니다.'))
        if self.reset_at_utc is not None:
            if self.reset_at_utc.utcoffset() is None:
                raise ValueError(tr('초기화 시각에는 시간대가 필요합니다.'))
            object.__setattr__(self, "reset_at_utc", self.reset_at_utc.astimezone(UTC))
        if self.window_seconds is not None and (
            type(self.window_seconds) is not int or self.window_seconds <= 0
        ):
            raise ValueError(tr('사용량 창은 양의 정수 초여야 합니다.'))

    @property
    def remaining_percent(self) -> float | None:
        return None if self.used_percent is None else max(0.0, 100.0 - self.used_percent)


def retry_after_seconds(header: str | None, received_at: datetime) -> float | None:
    if not header:
        return None
    try:
        if header.strip().isascii() and header.strip().isdigit():
            seconds = float(header.strip())
        else:
            when = parsedate_to_datetime(header)
            if when.utcoffset() is None:
                return None
            seconds = max(0.0, (when - received_at).total_seconds())
        return seconds if math.isfinite(seconds) else None
    except (ValueError, TypeError, OverflowError):
        return None


class RefreshPolicy:
    """Pure scheduling policy. Caller supplies monotonic time and positive jitter."""

    def __init__(self) -> None:
        self.generation = 0
        self.inflight = False
        self.failures = 0
        self.last_start = float("-inf")
        self.retry_at = 0.0
        self.next_auto = 0.0
        self.halted = True

    def begin(self, now: float, manual: bool = False) -> int | None:
        if self.halted or self.inflight or now < self.retry_at or now - self.last_start < 10:
            return None
        if not manual and now < self.next_auto:
            return None
        self.generation += 1
        self.inflight = True
        self.last_start = now
        return self.generation

    def finish(
        self,
        generation: int,
        now: float,
        error: str | None = None,
        retry_after: float | None = None,
        jitter: float = 0.0,
    ) -> bool:
        if generation != self.generation or not self.inflight:
            return False
        if not math.isfinite(jitter) or not 0 <= jitter <= 5:
            raise ValueError(tr('지터는 0~5초입니다.'))
        if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
            raise ValueError(tr('재시도 대기는 유한한 0 이상 값이어야 합니다.'))
        self.inflight = False
        if error is None:
            self.failures = 0
            self.retry_at = 0
            self.next_auto = now + 180
        elif error in {"AUTH_REQUIRED", "BLOCKED"}:
            self.halted = True
        elif error == "USAGE_UNAVAILABLE":
            # A successful HTTP response without a quota is not a server rate limit.
            # Keep the normal polling interval; allow a manual retry after 10 seconds.
            self.retry_at = 0
            self.next_auto = now + 180 + jitter
        else:
            self.failures += 1
            backoff = (60, 120, 240, 480, 900)[min(self.failures - 1, 4)]
            delay = retry_after if error == "RATE_LIMITED" and retry_after is not None else backoff
            self.retry_at = self.next_auto = now + delay + jitter
        return True

    def disconnect(self) -> None:
        self.generation += 1
        self.inflight = False
        self.halted = True

    def reconnect(self) -> None:
        self.disconnect()
        self.halted = False
        self.failures = 0
        self.last_start = float("-inf")
        self.retry_at = self.next_auto = 0
