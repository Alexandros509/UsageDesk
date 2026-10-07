import pytest
import win32security

from usagedesk.credentials import CredentialVault
from usagedesk.oauth import ProviderError


def test_real_dpapi_roundtrip_no_plaintext_and_protected_acl(tmp_path):
    vault = CredentialVault(tmp_path)
    secret = {"access_token": "sentinel-access-123", "refresh_token": "sentinel-refresh-456"}
    vault.save("claude", secret)
    path = tmp_path / "secrets" / "claude.dpapi"
    assert b"sentinel" not in path.read_bytes()
    assert vault.load("claude") == secret
    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    assert descriptor.GetSecurityDescriptorDacl().GetAceCount() == 2
    vault.delete("claude")
    assert vault.load("claude") is None


def test_corruption_fails_closed(tmp_path):
    vault = CredentialVault(tmp_path)
    vault.save("codex", {"access_token": "secret"})
    path = tmp_path / "secrets" / "codex.dpapi"
    path.write_bytes(b"corrupt")
    with pytest.raises(ProviderError, match="STORAGE_ERROR"):
        vault.load("codex")
    assert path.read_bytes() == b"corrupt"


def test_acl_failure_never_writes_plaintext(tmp_path, monkeypatch):
    vault = CredentialVault(tmp_path)

    def fail(*_):
        raise OSError("ACL failure")

    monkeypatch.setattr(vault, "_protect_acl", fail)
    with pytest.raises(ProviderError, match="STORAGE_ERROR"):
        vault.save("claude", {"access_token": "sentinel"})
    assert not list((tmp_path / "secrets").glob("*"))
