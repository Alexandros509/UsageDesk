import base64
import hashlib
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from usagedesk.oauth import CONFIGS, MANUAL_REDIRECT, LoopbackLogin, ProviderError, Transaction


def callback(tx, **changes):
    fields = {"state": tx.state, "code": "test-code"} | changes
    return tx.redirect_uri + "?" + urlencode(fields)


def test_pkce_state_and_single_use():
    tx = Transaction(CONFIGS["claude"], "http://localhost:1456/callback")
    other = Transaction(CONFIGS["claude"], tx.redirect_uri)
    assert tx.state != other.state and tx.verifier != other.verifier
    fields = parse_qs(urlsplit(tx.authorize_url()).query)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(tx.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert fields["code_challenge"] == [expected]
    assert fields["code_challenge_method"] == ["S256"]
    assert tx.verifier not in tx.authorize_url()
    assert tx.accept(callback(tx)) == "test-code"
    with pytest.raises(ProviderError, match="AUTH_EXPIRED"):
        tx.accept(callback(tx))


@pytest.mark.parametrize(
    "bad",
    [
        "https://localhost:1456/callback",
        "http://evil.test:1456/callback",
        "http://localhost:1457/callback",
        "http://localhost:1456/wrong",
        "http://user@localhost:1456/callback",
    ],
)
def test_callback_origin_and_path_rejected(bad):
    tx = Transaction(CONFIGS["claude"], "http://localhost:1456/callback")
    with pytest.raises(ProviderError, match="INVALID_CALLBACK"):
        tx.accept(bad + "?" + urlencode({"code": "x", "state": tx.state}))
    assert not tx.consumed


def test_wrong_missing_duplicate_state_and_expiry():
    tx = Transaction(CONFIGS["claude"], MANUAL_REDIRECT)
    for raw in [
        callback(tx, state="wrong"),
        tx.redirect_uri + "?code=x",
        callback(tx) + "&state=x",
        "bare-code",
    ]:
        with pytest.raises(ProviderError, match="INVALID_CALLBACK"):
            tx.accept(raw)
    tx.started = time.monotonic() - 301
    with pytest.raises(ProviderError, match="AUTH_EXPIRED"):
        tx.accept(callback(tx))


def test_manual_code_requires_state_and_specific_transaction():
    tx = Transaction(CONFIGS["claude"], MANUAL_REDIRECT)
    assert tx.accept("test-code#" + tx.state) == "test-code"
    automatic = Transaction(CONFIGS["claude"], "http://localhost:1456/callback")
    with pytest.raises(ProviderError):
        automatic.accept("test-code#" + automatic.state)


def test_real_loopback_rejects_wrong_host_then_accepts_valid_callback():
    config = replace(CONFIGS["claude"], ports=(0,))  # Ephemeral port for tests only.
    login = LoopbackLogin(config)
    try:
        url = callback(login.transaction).replace("localhost", "127.0.0.1")
        with httpx.Client(trust_env=False, timeout=3) as client:
            assert client.get(url).status_code == 400
            response = client.get(
                url, headers={"Host": urlsplit(login.transaction.redirect_uri).netloc}
            )
            assert response.status_code == 200
            assert "test-code" not in response.text
            assert response.headers["Cache-Control"] == "no-store"
        assert login.code == "test-code"
    finally:
        login.close()
        login.thread.join(3)
    assert not login.thread.is_alive()


def test_busy_port_does_not_reuse_another_listener():
    first = LoopbackLogin(replace(CONFIGS["claude"], ports=(0,)))
    try:
        port = first.server.server_port
        with pytest.raises(ProviderError, match="PORT_BUSY"):
            LoopbackLogin(replace(CONFIGS["claude"], ports=(port,)))
    finally:
        first.close()
        first.thread.join(3)
