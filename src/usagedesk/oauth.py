"""OAuth protocol adapted from Usage4Claude; see licenses/Usage4Claude-MIT.txt."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from .i18n import tr


class ProviderError(Exception):
    """Only fixed, non-sensitive error codes may cross the worker/UI boundary."""

    def __init__(self, code: str, retry_after: float | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class ProviderConfig:
    key: str
    label: str
    authorize_url: str
    token_url: str
    usage_url: str
    client_id: str
    scope: str
    ports: tuple[int, ...]
    callback_path: str


CONFIGS = {
    "claude": ProviderConfig(
        "claude",
        "Claude",
        "https://claude.ai/oauth/authorize",
        "https://console.anthropic.com/v1/oauth/token",
        "https://api.anthropic.com/api/oauth/usage",
        "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "user:profile",
        (1456, 1458),
        "/callback",
    ),
    "codex": ProviderConfig(
        "codex",
        tr('Codex · ChatGPT 계정'),
        "https://auth.openai.com/oauth/authorize",
        "https://auth.openai.com/oauth/token",
        "https://chatgpt.com/backend-api/wham/usage",
        "app_EMoamEEZ73f0CkXaXp7hrann",
        "openid profile email offline_access",
        (1455, 1457),
        "/auth/callback",
    ),
}
MANUAL_REDIRECT = "https://console.anthropic.com/oauth/code/callback"


@dataclass(repr=False)
class Transaction:
    config: ProviderConfig
    redirect_uri: str
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    started: float = field(default_factory=time.monotonic)
    consumed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started >= 300

    def authorize_url(self) -> str:
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        fields = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.config.scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": self.state,
        }
        if self.config.key == "codex":
            fields.update(
                id_token_add_organizations="true",
                codex_cli_simplified_flow="true",
                originator="usagedesk",
            )
        return self.config.authorize_url + "?" + urlencode(fields)

    def accept(self, raw: str) -> str:
        with self.lock:
            if self.expired or self.consumed:
                raise ProviderError("AUTH_EXPIRED")
            if len(raw) > 8192 or any(ord(c) < 32 for c in raw):
                raise ProviderError("INVALID_CALLBACK")
            try:
                if "://" in raw:
                    actual, expected = urlsplit(raw), urlsplit(self.redirect_uri)
                    if (actual.scheme, actual.netloc, actual.path) != (
                        expected.scheme,
                        expected.netloc,
                        expected.path,
                    ) or actual.fragment:
                        raise ValueError
                    values = parse_qs(actual.query, keep_blank_values=True, max_num_fields=16)
                elif self.redirect_uri == MANUAL_REDIRECT and raw.count("#") == 1:
                    code, state = raw.split("#")
                    values = {"code": [code], "state": [state]}
                else:
                    raise ValueError
                if any(len(v) != 1 for v in values.values()):
                    raise ValueError
                returned = values.get("state", [""])[0]
                if not secrets.compare_digest(returned.encode(), self.state.encode()):
                    raise ValueError
            except (ValueError, UnicodeError):
                raise ProviderError("INVALID_CALLBACK") from None
            if "error" in values:
                self.consumed = True
                raise ProviderError("AUTH_DENIED")
            code = values.get("code", [""])[0]
            if not code or len(code) > 4096:
                raise ProviderError("INVALID_CALLBACK")
            self.consumed = True
            return code


class LoopbackLogin:
    def __init__(self, config: ProviderConfig):
        owner = self
        self.code: str | None = None
        self.error: str | None = None
        self.closed = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(1)
                super().setup()

            def log_message(self, *_):
                pass  # Callback URLs contain secrets.

            def do_GET(self):
                status = 400
                try:
                    expected = urlsplit(owner.transaction.redirect_uri)
                    if self.headers.get_all("Host") != [
                        expected.netloc
                    ] or not self.path.startswith("/"):
                        raise ProviderError("INVALID_CALLBACK")
                    owner.code = owner.transaction.accept(f"http://{expected.netloc}{self.path}")
                    status = 200
                except ProviderError as exc:
                    if exc.code == "AUTH_DENIED":
                        owner.error = exc.code
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(
                    b"Return to UsageDesk." if status == 200 else b"Invalid or expired callback."
                )

        # Exclusive Windows bind: do not reuse another application's listener.
        self.server = None
        for port in config.ports:
            try:
                server = HTTPServer(("127.0.0.1", port), Handler, bind_and_activate=False)
                import socket

                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                server.allow_reuse_address = False
                server.server_bind()
                server.server_activate()
                self.server = server
                break
            except OSError:
                server.server_close()
        if self.server is None:
            raise ProviderError("PORT_BUSY")
        self.server.timeout = 0.2
        port = self.server.server_port
        self.transaction = Transaction(config, f"http://localhost:{port}{config.callback_path}")
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            while (
                not self.closed.is_set()
                and not self.transaction.expired
                and not self.transaction.consumed
            ):
                self.server.handle_request()
        finally:
            self.server.server_close()

    def close(self):
        self.closed.set()
        with self.transaction.lock:
            self.transaction.consumed = True
