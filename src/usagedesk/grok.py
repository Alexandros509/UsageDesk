"""Grok Build quota protocol: xai-org/grok-build 2bdd1d6a, extensions/billing.rs.

Borrow the CLI's current access token per request; never copy or rotate its secrets.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import UTC, datetime
from pathlib import Path

from .domain import QuotaLimit
from .oauth import ProviderError
from .providers import Snapshot, Tokens, Transport

GROK_CLI = Path.home() / ".grok" / "bin" / "grok.exe"
AUTH_SCOPE = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828"
BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def normalize_grok(body, now=None):
    if not isinstance(body, dict):
        raise ProviderError("PARSE_ERROR")
    config = body.get("config")
    if config is None:
        raise ProviderError("UNSUPPORTED")
    if not isinstance(config, dict):
        raise ProviderError("PARSE_ERROR")
    snapshot = Snapshot(now or datetime.now(UTC))
    percent = config.get("creditUsagePercent")
    legacy = percent is None
    if legacy:
        limit, used = config.get("monthlyLimit"), config.get("used")
        if not isinstance(limit, dict) or not isinstance(used, dict):
            raise ProviderError("UNSUPPORTED")

        def cents(item):
            value = item.get("val", 0)  # An explicitly empty proto Cent represents zero.
            if type(value) is int:
                return value
            if isinstance(value, str) and value.isascii() and value.isdigit():
                return int(value)
            raise ProviderError("PARSE_ERROR")

        ceiling, consumed = cents(limit), cents(used)
        if ceiling <= 0 or consumed < 0:
            raise ProviderError("UNSUPPORTED")
        percent = consumed / ceiling * 100
    if type(percent) not in (int, float) or not math.isfinite(percent) or percent < 0:
        raise ProviderError("PARSE_ERROR")
    period = config.get("currentPeriod")
    if period is not None and not isinstance(period, dict):
        raise ProviderError("PARSE_ERROR")
    period = period or {}
    weekly = period.get("type") == "USAGE_PERIOD_TYPE_WEEKLY"
    monthly = period.get("type") == "USAGE_PERIOD_TYPE_MONTHLY" or legacy
    title = "주간 한도" if weekly else "월간 한도" if monthly else "계정 한도"
    if config.get("isUnifiedBillingUser") is True:
        title = "통합 " + title
        snapshot.notes.append("Grok 제품들이 공유하는 계정 한도입니다. Build 전용 토큰 수가 아닙니다.")
    reset = period.get("end") or config.get("billingPeriodEnd")
    moment = None
    if reset is not None:
        try:
            moment = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            if moment.utcoffset() is None:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            snapshot.warnings.append("초기화 시각 형식이 올바르지 않아 시각을 표시하지 않습니다.")
            moment = None
    snapshot.limits.append(QuotaLimit("account", title, percent, moment,
                                      604800 if weekly else None))
    snapshot.notes.append("Grok CLI 인증 사용 · 인증 만료 시 CLI 로그인 후 다시 연결하세요.")
    return snapshot


class GrokSession:
    def __init__(self, vault, auth_path=None, transport=None):
        self.vault = vault
        self.auth_path = auth_path or Path.home() / ".grok" / "auth.json"
        self.transport = transport or Transport()
        self.tokens = None  # Controller link state, not a token cache.
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
                self.vault.delete("grok")  # Only our preference; never CLI auth.json.
            return self.epoch

    def check(self, epoch):
        if epoch != self.epoch or self.cancel.is_set():
            raise ProviderError("CANCELLED")

    def restore(self, epoch):
        with self.lock:
            self.check(epoch)
            stored = self.vault.load("grok")
            self.tokens = True if isinstance(stored, dict) and stored.get("cli_link") is True else None
            return self.tokens is not None

    def connect(self, epoch):
        with self.lock:
            self.check(epoch)
            self.vault.save("grok", {"cli_link": True})
            self.tokens = True
        return self.fetch(epoch)

    def credential(self):
        try:
            with self.auth_path.open("rb") as file:
                raw = file.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError
            store = json.loads(raw)
            auth = store.get(AUTH_SCOPE) if isinstance(store, dict) else None
            if not isinstance(auth, dict):
                raise ProviderError("AUTH_REQUIRED")
            if (auth.get("oidc_issuer") != "https://auth.x.ai"
                    or auth.get("oidc_client_id") != AUTH_SCOPE.split("::")[1]):
                raise ProviderError("AUTH_REQUIRED")
            if auth.get("principal_type") not in (None, "", "User"):
                raise ProviderError("UNSUPPORTED")
            expires = auth.get("expires_at")
            if expires:
                expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if expiry.utcoffset() is None or expiry <= datetime.now(UTC):
                    raise ProviderError("AUTH_REQUIRED")
            return Tokens.parse({"access_token": auth.get("key"),
                                 "account_id": auth.get("user_id")})
        except FileNotFoundError:
            raise ProviderError("AUTH_REQUIRED") from None
        except (ValueError, TypeError, AttributeError, OSError):
            raise ProviderError("STORAGE_ERROR") from None

    def fetch(self, epoch):
        with self.lock:
            self.check(epoch)
            if self.tokens is None:
                raise ProviderError("CANCELLED")
            cancel = self.cancel
        tokens = self.credential()
        body = self.transport.request("GET", BILLING_URL, cancel=cancel, headers={
            "Authorization": f"Bearer {tokens.access_token}",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-userid": tokens.account_id,
            "x-grok-client-mode": "cli",
        })
        with self.lock:
            self.check(epoch)
        return normalize_grok(body)
