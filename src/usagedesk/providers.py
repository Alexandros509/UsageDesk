"""Network and response adapters derived from the pinned reference protocol."""

from __future__ import annotations

import base64
import json
import math
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from .domain import QuotaLimit, retry_after_seconds
from .i18n import tr
from .oauth import CONFIGS, ProviderConfig, ProviderError, Transaction


@dataclass(repr=False)
class Tokens:
    access_token: str
    refresh_token: str = ""
    expires_at: float | None = None
    account_id: str = ""

    @classmethod
    def parse(cls, body: dict, previous: Tokens | None = None) -> Tokens:
        def secret(value):
            if (
                not isinstance(value, str)
                or len(value) > 16384
                or any(ord(c) < 33 or ord(c) > 126 for c in value)
            ):
                raise ProviderError("PARSE_ERROR")
            return value

        access = secret(body.get("access_token", ""))
        if not access:
            raise ProviderError("PARSE_ERROR")
        refresh = secret(body.get("refresh_token") or (previous.refresh_token if previous else ""))
        expiry = body.get("expires_at")
        if expiry is None and type(body.get("expires_in")) in (int, float):
            expiry = time.time() + body["expires_in"]
        claims = {}
        try:
            part = access.split(".")[1]
            decoded = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            if isinstance(decoded, dict):
                claims = decoded
        except (ValueError, IndexError, UnicodeError):
            pass
        # JWT claims are hints from the received token, never an authentication proof.
        expiry = expiry if expiry is not None else claims.get("exp")
        if type(expiry) not in (int, float) or not math.isfinite(expiry):
            expiry = None
        auth = claims.get("https://api.openai.com/auth", {})
        account = auth.get("chatgpt_account_id", "") if isinstance(auth, dict) else ""
        account = body.get("account_id") or account or (previous.account_id if previous else "")
        if (
            not isinstance(account, str)
            or len(account) > 256
            or any(ord(c) < 33 or ord(c) > 126 for c in account)
        ):
            account = ""
        return cls(access, refresh, expiry, account)


class Transport:
    def __init__(self, client_factory=httpx.Client):
        self.client_factory = client_factory

    def request(self, method, url, *, cancel=None, **kwargs):
        started = time.monotonic()
        cancel = cancel or threading.Event()
        try:
            with self.client_factory(
                timeout=httpx.Timeout(15, connect=5),
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": "UsageDesk/0.1", "Accept": "application/json"},
            ) as client:
                if cancel.is_set():
                    raise ProviderError("CANCELLED")
                with client.stream(method, url, **kwargs) as response:
                    received_at = datetime.now(UTC)
                    status = response.status_code
                    retry = retry_after_seconds(response.headers.get("Retry-After"), received_at)
                    if status == 429:
                        raise ProviderError("RATE_LIMITED", retry)
                    if status == 401:
                        raise ProviderError("AUTH_REQUIRED")
                    if status == 403:
                        raise ProviderError("BLOCKED")
                    if status >= 500:
                        raise ProviderError("SERVER_ERROR")
                    if 300 <= status < 400:
                        raise ProviderError("BLOCKED")  # Never forward credentials to redirects.
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        if cancel.is_set():
                            raise ProviderError("CANCELLED")
                        if time.monotonic() - started > 25:
                            raise ProviderError("TIMEOUT")
                        data.extend(chunk)
                        if len(data) > 1024 * 1024:
                            raise ProviderError("PARSE_ERROR")
                    if cancel.is_set():
                        raise ProviderError("CANCELLED")
                    if time.monotonic() - started > 25:
                        raise ProviderError("TIMEOUT")
                    try:
                        body = json.loads(data)
                    except (ValueError, UnicodeError):
                        raise ProviderError(
                            "PARSE_ERROR" if status < 400 else "AUTH_REQUIRED"
                        ) from None
                    if not isinstance(body, dict):
                        raise ProviderError("PARSE_ERROR")
                    if status >= 400:
                        code = body.get("error")
                        if isinstance(code, dict):
                            code = code.get("code")
                        dead = {
                            "invalid_grant",
                            "refresh_token_invalidated",
                            "refresh_token_expired",
                            "refresh_token_reused",
                        }
                        raise ProviderError(
                            "AUTH_REQUIRED"
                            if isinstance(code, str) and code in dead
                            else "REQUEST_REJECTED"
                        )
                    return body
        except httpx.TimeoutException:
            raise ProviderError("TIMEOUT") from None
        except httpx.HTTPError:
            raise ProviderError("OFFLINE") from None


@dataclass
class Snapshot:
    fetched_at: datetime
    limits: list[QuotaLimit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    extra_usage_enabled: bool = False


def normalize_usage(provider: str, body: dict, now: datetime | None = None) -> Snapshot:
    snapshot = Snapshot(now or datetime.now(UTC))
    if not isinstance(body, dict):
        raise ProviderError("PARSE_ERROR")

    def append(key, label, value, reset=None, seconds=None, relative=None):
        if value is None:
            return
        try:
            moment = None
            if reset is not None:
                if isinstance(reset, str):
                    moment = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                elif type(reset) in (int, float) and math.isfinite(reset):
                    moment = datetime.fromtimestamp(reset, UTC)
                else:
                    raise ValueError
            elif relative is not None:
                if (
                    type(relative) not in (int, float)
                    or not math.isfinite(relative)
                    or relative < 0
                ):
                    raise ValueError
                moment = snapshot.fetched_at + timedelta(seconds=relative)
                label += tr(' · 초기화 시각 계산값')
            quota = QuotaLimit(key, str(label)[:100], value, moment, seconds)
            existing = next((q for q in snapshot.limits if q.key == key), None)
            if existing is None:
                snapshot.limits.append(quota)
            elif (existing.used_percent, existing.reset_at_utc) != (value, moment):
                snapshot.warnings.append(tr('동일 한도의 응답 값이 달라 먼저 받은 유효한 요약 값을 표시합니다.'))
        except (ValueError, TypeError, OverflowError, OSError):
            snapshot.warnings.append(tr('일부 제한 항목의 형식이 올바르지 않습니다.'))

    if provider == "claude":
        names = {
            "five_hour": tr('5시간'),
            "seven_day": tr('7일'),
            "seven_day_opus": tr('Opus · 7일'),
            "seven_day_sonnet": tr('Sonnet · 7일'),
            "seven_day_oauth_apps": tr('OAuth 앱 · 7일'),
        }
        for key, label in names.items():
            item = body.get(key)
            if item is not None and not isinstance(item, dict):
                snapshot.warnings.append(tr('일부 제한 항목의 형식이 올바르지 않습니다.'))
            elif item:
                append(
                    key,
                    label,
                    item.get("utilization"),
                    item.get("resets_at"),
                    18000 if key == "five_hour" else 604800,
                )
        items = body.get("limits") or []
        if not isinstance(items, list):
            snapshot.warnings.append(tr('제한 목록 형식이 올바르지 않습니다.'))
            items = []
        for i, item in enumerate(items[:100]):
            if not isinstance(item, dict):
                snapshot.warnings.append(tr('일부 제한 항목의 형식이 올바르지 않습니다.'))
                continue
            scope = item.get("scope") or {}
            model = scope.get("model") if isinstance(scope, dict) else None
            model = model if isinstance(model, dict) else {}
            kind = str(item.get("kind") or "unknown")
            model_id = model.get("id")
            key = f"{kind}:{model_id}" if isinstance(model_id, str) and model_id else f"limit:{i}"
            canonical = {"session": "five_hour", "weekly_all": "seven_day"}.get(kind)
            # Only unscoped account-wide windows are aliases of the legacy fields.
            # Model/surface-specific quotas remain independent even if percentages match.
            if canonical and isinstance(scope, dict) and not any(scope.values()):
                snapshot.aliases[key] = canonical
                key = canonical
            elif isinstance(scope, dict) and scope.get("surface"):
                key += f":surface:{scope['surface']}"
            label = model.get("display_name") or {
                "session": tr('세션 제한'),
                "weekly_all": tr('전체 주간 제한'),
                "weekly_scoped": tr('모델별 주간 제한'),
            }.get(kind, tr('추가 제한'))
            if key in ("five_hour", "seven_day"):
                label = names[key]
            size = 18000 if kind == "session" else 604800 if kind.startswith("weekly_") else None
            append(key, label, item.get("percent"), item.get("resets_at"), size)
        extra = body.get("extra_usage")
        if isinstance(extra, dict) and extra.get("is_enabled") is True:
            snapshot.extra_usage_enabled = True
            append("extra", tr('추가 사용량'), extra.get("utilization"))
            snapshot.notes.append(tr('추가 사용량 활성 · 금액 단위는 아직 표시하지 않습니다.'))
    else:
        rate = body.get("rate_limit") or {}
        if not isinstance(rate, dict):
            raise ProviderError("PARSE_ERROR")
        for key, fallback in (("primary_window", tr('주 제한')), ("secondary_window", tr('보조 제한'))):
            item = rate.get(key)
            if item is None:
                continue
            if not isinstance(item, dict):
                snapshot.warnings.append(tr('일부 제한 항목의 형식이 올바르지 않습니다.'))
                continue
            size = item.get("limit_window_seconds")
            label = (
                {18000: tr('5시간'), 604800: tr('7일')}.get(
                    size, tr('{p0}초 제한', p0=size) if type(size) is int else fallback
                )
                if type(size) in (int, type(None))
                else fallback
            )
            append(
                key,
                label,
                item.get("used_percent"),
                item.get("reset_at"),
                size,
                item.get("reset_after_seconds"),
            )
        credits = body.get("credits")
        if isinstance(credits, dict):
            if credits.get("unlimited") is True:
                snapshot.notes.append(tr('크레딧: 무제한'))
            elif credits.get("balance") is not None:
                try:
                    balance = Decimal(str(credits["balance"]))
                    if not balance.is_finite() or balance < 0:
                        raise ValueError
                    snapshot.notes.append(tr('남은 크레딧: {p0}', p0=balance))
                except (ValueError, InvalidOperation):
                    snapshot.warnings.append(tr('크레딧 형식이 올바르지 않습니다.'))
    if not snapshot.limits and not snapshot.notes:
        raise ProviderError("PARSE_ERROR" if snapshot.warnings else "UNSUPPORTED")
    return snapshot


class Provider:
    def __init__(self, config: ProviderConfig, transport=None):
        self.config = config
        self.transport = transport or Transport()

    def exchange(self, tx: Transaction, code: str, cancel=None) -> Tokens:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": tx.redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": tx.verifier,
        }
        if self.config.key == "claude":
            payload["state"] = tx.state
        body = self.transport.request(
            "POST",
            self.config.token_url,
            cancel=cancel,
            **{("json" if self.config.key == "claude" else "data"): payload},
        )
        return Tokens.parse(body)

    def refresh(self, tokens: Tokens, cancel=None) -> Tokens:
        if not tokens.refresh_token:
            raise ProviderError("AUTH_REQUIRED")
        body = self.transport.request(
            "POST",
            self.config.token_url,
            cancel=cancel,
            json={
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "refresh_token": tokens.refresh_token,
            },
        )
        return Tokens.parse(body, tokens)

    def usage(self, tokens: Tokens, cancel=None) -> Snapshot:
        headers = {"Authorization": f"Bearer {tokens.access_token}"}
        if self.config.key == "claude":
            headers["anthropic-beta"] = "oauth-2025-04-20"
        elif tokens.account_id:
            headers["ChatGPT-Account-Id"] = tokens.account_id
        body = self.transport.request("GET", self.config.usage_url, headers=headers, cancel=cancel)
        return normalize_usage(self.config.key, body)


class AccountSession:
    """One worker per provider, with logout-safe credential persistence."""

    def __init__(self, key, vault, provider=None):
        self.key, self.vault = key, vault
        self.provider = provider or Provider(CONFIGS[key])
        self.tokens: Tokens | None = None
        self.epoch = 0
        self.lock = threading.RLock()
        self.cancel = threading.Event()

    def reset(self, delete=False):
        with self.lock:
            self.cancel.set()
            self.cancel = threading.Event()
            self.epoch += 1
            self.tokens = None
            if delete:
                self.vault.delete(self.key)
            return self.epoch

    def commit(self, epoch, tokens):
        with self.lock:
            if epoch != self.epoch or self.cancel.is_set():
                raise ProviderError("CANCELLED")
            self.vault.save(self.key, asdict(tokens))
            self.tokens = tokens

    def restore(self, epoch):
        with self.lock:
            if epoch != self.epoch:
                raise ProviderError("CANCELLED")
            data = self.vault.load(self.key)
            self.tokens = Tokens.parse(data) if data else None
            return self.tokens is not None

    def login(self, epoch, tx, code):
        cancel = self.cancel
        tokens = self.provider.exchange(tx, code, cancel)
        self.commit(epoch, tokens)
        return self.fetch(epoch)

    def fetch(self, epoch):
        with self.lock:
            if epoch != self.epoch or self.tokens is None:
                raise ProviderError("CANCELLED")
            tokens, cancel = self.tokens, self.cancel
        refreshed = False
        if tokens.expires_at is not None and tokens.expires_at <= time.time() + 30:
            tokens = self.provider.refresh(tokens, cancel)
            self.commit(epoch, tokens)  # Persist rotation BEFORE the usage request.
            refreshed = True
        try:
            return self.provider.usage(tokens, cancel)
        except ProviderError as exc:
            if exc.code != "AUTH_REQUIRED" or refreshed:
                raise
            tokens = self.provider.refresh(tokens, cancel)
            self.commit(epoch, tokens)
            return self.provider.usage(tokens, cancel)
