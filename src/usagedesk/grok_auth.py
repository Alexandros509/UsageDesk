"""CLI-compatible Windows auth lock and atomic, private credential replacement.

Protocol reference: xai-org/grok-build 2bdd1d6a, xai-grok-login/manager/lock.rs.
The lock file is persistent: never unlink a lock that another CLI may have open.
"""

import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .credentials import CredentialVault
from .oauth import ProviderError
from .storage import unique_fields


class GrokAuthStore:
    def __init__(self, path):
        self.path = Path(path)

    def revision(self):
        try:
            value = self.path.stat()
            return value.st_mtime_ns, value.st_size, value.st_ino
        except OSError:
            return None

    def read(self):
        try:
            with self.path.open('rb') as source:
                raw = source.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError
            result = json.loads(raw, object_pairs_hook=unique_fields)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except FileNotFoundError:
            raise ProviderError('AUTH_REQUIRED') from None
        except (OSError, ValueError, TypeError, RecursionError):
            raise ProviderError('STORAGE_ERROR') from None

    @contextmanager
    def refresh_lock(self, cancel, timeout=10):
        import pywintypes
        import win32con
        import win32file

        lock_path = self.path.with_name('auth.json.lock')
        handle = None
        acquired = False
        stop = threading.Event()
        heartbeat = None
        overlap = pywintypes.OVERLAPPED()
        try:
            # Decline redirects when writing the CLI's secrets or lock.
            for path in (self.path, lock_path, self.path.parent):
                if path.exists() and path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ProviderError('STORAGE_ERROR')
            handle = win32file.CreateFile(str(lock_path), win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                         win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE
                                         | win32con.FILE_SHARE_DELETE, None, win32con.OPEN_ALWAYS, 0, None)
            deadline = time.monotonic() + timeout
            while not acquired:
                if cancel.is_set():
                    raise ProviderError('CANCELLED')
                try:
                    # fs2's Windows exclusive flock covers the same full byte range.
                    win32file.LockFileEx(handle, 3, -1, -1, overlap)
                    acquired = True
                except pywintypes.error as exc:
                    if exc.winerror != 33:  # ERROR_LOCK_VIOLATION
                        raise
                    if time.monotonic() >= deadline:
                        raise ProviderError('AUTH_BUSY') from None
                    cancel.wait(.05)

            def stamp():
                win32file.SetFilePointer(handle, 0, win32con.FILE_BEGIN)
                win32file.SetEndOfFile(handle)
                win32file.WriteFile(handle, f'{os.getpid()}:{int(time.time())}'.encode('ascii'))
                win32file.FlushFileBuffers(handle)

            stamp()
            def maintain():
                while not stop.wait(5):
                    try:
                        stamp()
                    except (OSError, pywintypes.error):
                        break
            heartbeat = threading.Thread(target=maintain, daemon=True)
            heartbeat.start()
            yield
        except (OSError, pywintypes.error):
            raise ProviderError('STORAGE_ERROR') from None
        finally:
            stop.set()
            if heartbeat is not None:
                heartbeat.join()
            try:
                if acquired:
                    win32file.UnlockFileEx(handle, -1, -1, overlap)
            finally:
                if handle is not None:
                    handle.Close()

    def replace_auth(self, scope, expected, refreshed):
        """Called under refresh_lock; preserve other scopes and deny login races."""
        temp = None
        try:
            current = self.read()
            if current.get(scope) != expected:
                raise ProviderError('STORAGE_ERROR')
            current[scope] = refreshed
            content = json.dumps(current, ensure_ascii=False).encode('utf-8')
            if len(content) > 1024 * 1024:
                raise ProviderError('STORAGE_ERROR')
            fd, temp = tempfile.mkstemp(prefix='auth-', suffix='.tmp', dir=self.path.parent)
            with os.fdopen(fd, 'wb') as target:
                CredentialVault(self.path.parent)._protect_acl(Path(temp))
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp, self.path)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('STORAGE_ERROR') from None
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
