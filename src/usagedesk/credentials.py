"""Current-user DPAPI vault; no plain-text fallback, no backups of old tokens."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .oauth import CONFIGS, ProviderError


class CredentialVault:
    def __init__(self, directory: Path):
        self.directory = directory / "secrets"

    def _path(self, provider: str) -> Path:
        if provider not in CONFIGS and provider != "grok":
            raise ProviderError("STORAGE_ERROR")
        return self.directory / f"{provider}.dpapi"

    def _protect_acl(self, path: Path, directory: bool = False):
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        acl = win32security.ACL()
        flags = (win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE) if directory else 0
        for principal in (sid, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)):
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION, flags, win32con.GENERIC_ALL, principal
            )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )

    def save(self, provider: str, data: dict):
        import win32crypt

        temp = None
        try:
            target = self._path(provider)
            self.directory.mkdir(parents=True, exist_ok=True)
            self._protect_acl(self.directory, True)
            encrypted = win32crypt.CryptProtectData(
                json.dumps(data).encode(), "UsageDesk", None, None, None, 1
            )
            fd, temp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            with os.fdopen(fd, "wb") as output:
                self._protect_acl(Path(temp))
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, target)
        except Exception:
            raise ProviderError("STORAGE_ERROR") from None
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def load(self, provider: str) -> dict | None:
        import win32crypt

        path = self._path(provider)
        try:
            if not path.exists():
                return None
            self._protect_acl(self.directory, True)
            self._protect_acl(path)
            with path.open("rb") as source:
                encrypted = source.read(65537)
            if len(encrypted) > 65536:
                raise ValueError
            raw = win32crypt.CryptUnprotectData(encrypted, None, None, None, 1)[1]
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except Exception:
            raise ProviderError("STORAGE_ERROR") from None

    def delete(self, provider: str):
        try:
            self._path(provider).unlink(missing_ok=True)
        except OSError:
            raise ProviderError("STORAGE_ERROR") from None
