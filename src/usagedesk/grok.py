"""Grok Build quota protocol: xai-org/grok-build 2bdd1d6a, extensions/billing.rs.

Read CLI credentials per request; refresh under the CLI's shared auth lock.
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .domain import QuotaLimit
from .grok_auth import GrokAuthStore
from .i18n import tr
from .oauth import ProviderError
from .providers import Snapshot, Tokens, Transport

GROK_CLI = Path.home() / ".grok" / "bin" / "grok.exe"
AUTH_SCOPE = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828"
BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
TOKEN_URL = 'https://auth.x.ai/oauth2/token'


def normalize_grok(body, now=None):
    if not isinstance(body, dict):
        raise ProviderError("PARSE_ERROR")
    config = body.get("config")
    if config is None:
        raise ProviderError("USAGE_UNAVAILABLE")
    if not isinstance(config, dict):
        raise ProviderError("PARSE_ERROR")
    snapshot = Snapshot(now or datetime.now(UTC))
    percent = config.get("creditUsagePercent")
    legacy = percent is None
    if legacy:
        limit, used = config.get("monthlyLimit"), config.get("used")
        if not isinstance(limit, dict) or not isinstance(used, dict):
            raise ProviderError("USAGE_UNAVAILABLE")

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
    title = tr('주간 한도') if weekly else tr('월간 한도') if monthly else tr('계정 한도')
    if config.get("isUnifiedBillingUser") is True:
        title = tr('통합 ') + title
        snapshot.notes.append(tr('Grok 제품들이 공유하는 계정 한도입니다. Build 전용 토큰 수가 아닙니다.'))
    reset = period.get("end") or config.get("billingPeriodEnd")
    moment = None
    if reset is not None:
        try:
            moment = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            if moment.utcoffset() is None:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            snapshot.warnings.append(tr('초기화 시각 형식이 올바르지 않아 시각을 표시하지 않습니다.'))
            moment = None
    snapshot.limits.append(QuotaLimit("account", title, percent, moment,
                                      604800 if weekly else None))
    snapshot.notes.append(tr('Grok CLI 인증 사용 · 만료 전 자동 갱신하며 갱신 불가 시 다시 로그인하세요.'))
    return snapshot


class GrokSession:
    def __init__(self, vault, auth_path=None, transport=None):
        self.vault = vault
        self.auth_path = auth_path or Path.home() / ".grok" / "auth.json"
        self.auth_store = GrokAuthStore(self.auth_path)
        self.transport = transport or Transport()
        self.tokens = None  # Controller link state, not a token cache.
        self.epoch = 0
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.refresh_serial = 0
        self.pending_auth = None  # Retain a rotated response if disk persistence fails.

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

    def read_auth(self):
        try:
            auth = self.auth_store.read().get(AUTH_SCOPE)
            if not isinstance(auth, dict):
                raise ProviderError("AUTH_REQUIRED")
            if (auth.get("oidc_issuer") != "https://auth.x.ai"
                    or auth.get("oidc_client_id") != AUTH_SCOPE.split("::")[1]):
                raise ProviderError("AUTH_REQUIRED")
            if auth.get("principal_type") not in (None, "", "User"):
                raise ProviderError("UNSUPPORTED")
            expires = auth.get("expires_at")
            expiry = None
            if expires:
                expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if expiry.utcoffset() is None:
                    raise ValueError
            tokens = Tokens.parse({'access_token': auth.get('key'), 'account_id': auth.get('user_id')})
            if expiry is None and tokens.expires_at is not None:
                expiry = datetime.fromtimestamp(tokens.expires_at, UTC)
            return auth, tokens, expiry
        except (ValueError, TypeError, AttributeError, OSError, OverflowError):
            raise ProviderError("STORAGE_ERROR") from None

    def credential(self, *, rejected_access=None, cancel=None):
        cancel = cancel or self.cancel
        auth, tokens, expiry = self.read_auth()
        now = datetime.now(UTC)
        rejected = rejected_access == tokens.access_token
        due = expiry is not None and expiry <= now + timedelta(minutes=5)
        if not self.pending_auth and not rejected and not due:
            return tokens
        with self.auth_store.refresh_lock(cancel):
            if self.pending_auth:
                expected, refreshed = self.pending_auth
                if self.auth_store.read().get(AUTH_SCOPE) == expected:
                    self.auth_store.replace_auth(AUTH_SCOPE, expected, refreshed)
                self.pending_auth = None
            # A sibling CLI may have refreshed while this process waited for the lock.
            auth, tokens, expiry = self.read_auth()
            now = datetime.now(UTC)
            rejected = rejected_access == tokens.access_token
            due = expiry is not None and expiry <= now + timedelta(minutes=5)
            if not rejected and not due:
                return tokens
            refresh = auth.get('refresh_token')
            if not isinstance(refresh, str) or not refresh:
                if rejected or expiry is not None and expiry <= now:
                    raise ProviderError('AUTH_REQUIRED')
                return tokens
            previous = Tokens.parse({'access_token': tokens.access_token,
                                     'refresh_token': refresh, 'account_id': tokens.account_id})
            if cancel.is_set():
                raise ProviderError('CANCELLED')
            data = {'grant_type': 'refresh_token', 'refresh_token': previous.refresh_token,
                    'client_id': AUTH_SCOPE.split('::')[1]}
            for key in ('principal_type', 'principal_id'):
                if auth.get(key):
                    data[key] = auth[key]
            # Once sent, finish and persist rotation even if the UI disconnects.
            # Throwing away an in-flight rotated token could invalidate the CLI session.
            response = self.transport.request('POST', TOKEN_URL, data=data)
            fresh = Tokens.parse(dict(response, account_id=tokens.account_id), previous)
            moment = None
            if fresh.expires_at is not None:
                try:
                    moment = datetime.fromtimestamp(fresh.expires_at, UTC)
                    if moment <= datetime.now(UTC):
                        raise ValueError
                except (ValueError, OverflowError, OSError):
                    raise ProviderError('PARSE_ERROR') from None
            updated = dict(auth, key=fresh.access_token, refresh_token=fresh.refresh_token,
                           expires_at=moment.isoformat() if moment else None,
                           create_time=datetime.now(UTC).isoformat())
            self.pending_auth = (auth, updated)
            self.auth_store.replace_auth(AUTH_SCOPE, auth, updated)
            self.pending_auth = None
            self.refresh_serial += 1
            return fresh

    def fetch(self, epoch):
        with self.lock:
            self.check(epoch)
            if self.tokens is None:
                raise ProviderError("CANCELLED")
            cancel = self.cancel
        before = self.refresh_serial
        tokens = self.credential(cancel=cancel)
        def billing():
            return self.transport.request("GET", BILLING_URL, cancel=cancel, headers={
                "Authorization": f"Bearer {tokens.access_token}",
                "X-XAI-Token-Auth": "xai-grok-cli",
                "x-userid": tokens.account_id,
                "x-grok-client-mode": "cli",
            })
        try:
            body = billing()
        except ProviderError as exc:
            if exc.code != 'AUTH_REQUIRED' or before != self.refresh_serial:
                raise
            tokens = self.credential(rejected_access=tokens.access_token, cancel=cancel)
            body = billing()  # At most one refresh and one billing retry per poll.
        with self.lock:
            self.check(epoch)
        return normalize_grok(body)
