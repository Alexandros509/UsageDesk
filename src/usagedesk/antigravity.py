"""Antigravity quotas through the official CLI's /usage JSON command.

Protocol reference: antigravity.google/docs/cli/commands/usage/ and
lobner/gemini-usage (command.data.groups). No OAuth client impersonation,
browser cookie extraction, model prompts, or direct Google API requests.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from .domain import QuotaLimit
from .i18n import tr
from .oauth import ProviderError
from .providers import Snapshot

INSTALL_URL = 'https://antigravity.google/docs/getting-started'
OUTPUT_LIMIT = 1024 * 1024


def find_cli():
    """Use the official Windows install location and absolute PATH entries."""
    candidates = [os.environ.get('AGY_BIN', '')]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidates.append(str(Path(local) / 'agy' / 'bin' / 'agy.exe'))
    candidates.extend([str(Path.home() / '.local' / 'bin' / 'agy.exe'),
                       shutil.which('agy.exe') or ''])
    return next((Path(p) for p in candidates if p and Path(p).is_absolute()
                 and Path(p).is_file() and Path(p).suffix.casefold() == '.exe'), None)


def validate_cli(value):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ProviderError('AGY_NOT_FOUND')
    path = Path(value)
    if not path.is_absolute() or path.suffix.casefold() != '.exe' or not path.is_file():
        raise ProviderError('AGY_NOT_FOUND')
    return path


def normalize_antigravity(body, now=None):
    snapshot = Snapshot(now or datetime.now(UTC))
    if not isinstance(body, dict):
        raise ProviderError('PARSE_ERROR')
    status = body.get('status')
    if status != 'SUCCESS':
        error = body.get('error')
        code = error.get('code') if isinstance(error, dict) else error
        if code in ('UNAUTHENTICATED', 'AUTH_REQUIRED', 'NOT_LOGGED_IN'):
            raise ProviderError('AUTH_REQUIRED')
        if code in ('RESOURCE_EXHAUSTED', 'RATE_LIMITED'):
            raise ProviderError('RATE_LIMITED')
        raise ProviderError('AGY_COMMAND_FAILED')
    command = body.get('command')
    if not isinstance(command, dict):
        # Never accept an agent's generated response as authoritative usage.
        raise ProviderError('PARSE_ERROR')
    if command.get('name', 'usage') not in ('usage', '/usage', 'quota', '/quota'):
        raise ProviderError('PARSE_ERROR')
    data = command.get('data')
    groups = data.get('groups') if isinstance(data, dict) else None
    if not isinstance(groups, list) or len(groups) > 32:
        raise ProviderError('PARSE_ERROR')
    seen, pool_order = {}, {}
    for group in groups:
        if not isinstance(group, dict):
            raise ProviderError('PARSE_ERROR')
        name, buckets = group.get('name'), group.get('buckets')
        if (not isinstance(name, str) or not name.strip() or len(name) > 200
                or any(ord(c) < 32 for c in name)
                or not isinstance(buckets, list) or len(buckets) > 32):
            raise ProviderError('PARSE_ERROR')
        short = ('Claude/GPT' if 'claude' in name.casefold() and 'gpt' in name.casefold()
                 else 'Gemini' if name.casefold().strip() in ('gemini', 'gemini models')
                 else re.sub(r'^gemini\s+(?:\d+(?:\.\d+)?\s+)?', '', name, flags=re.I))
        short = short.strip()[:32]
        pool = hashlib.sha256(name.casefold().strip().encode()).hexdigest()[:16]
        pool_order.setdefault(pool, len(pool_order))
        for bucket in buckets:
            if not isinstance(bucket, dict):
                raise ProviderError('PARSE_ERROR')
            if bucket.get('enabled') is False:
                continue
            identifier = bucket.get('id')
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', identifier):
                raise ProviderError('PARSE_ERROR')
            remaining = bucket.get('remaining_fraction')
            if remaining is not None and (type(remaining) not in (int, float)
                    or not math.isfinite(remaining) or not 0 <= remaining <= 1):
                raise ProviderError('PARSE_ERROR')
            window = bucket.get('window') or ''
            if not isinstance(window, str):
                raise ProviderError('PARSE_ERROR')
            window = window.casefold().replace(' ', '_')
            seconds = (18000 if window in ('5h', 'five_hour', 'five_hours', '5_hour', '5_hours')
                       else 604800 if window in ('weekly', 'week', '7d', 'seven_day', 'seven_days')
                       else None)
            period = '5h' if seconds == 18000 else '7d' if seconds == 604800 else tr('한도')
            reset = bucket.get('reset_time')
            moment = None
            if reset:
                try:
                    moment = datetime.fromisoformat(reset.replace('Z', '+00:00'))
                    if moment.utcoffset() is None:
                        raise ValueError
                except (ValueError, TypeError, AttributeError):
                    raise ProviderError('PARSE_ERROR') from None
            quota = QuotaLimit(f'agy/{identifier}', f'{short} · {period}',
                               None if remaining is None else (1 - remaining) * 100,
                               moment, seconds)
            if identifier in seen:
                previous = seen[identifier]
                if (previous.used_percent, previous.reset_at_utc, previous.window_seconds) != (
                        quota.used_percent, quota.reset_at_utc, quota.window_seconds):
                    raise ProviderError('PARSE_ERROR')
                continue  # The same explicitly identified shared bucket is shown once.
            seen[identifier] = quota
            snapshot.quota_groups[quota.key] = pool
            snapshot.limits.append(quota)
    if not snapshot.limits:
        raise ProviderError('USAGE_UNAVAILABLE')
    snapshot.limits.sort(key=lambda q: (pool_order[snapshot.quota_groups[q.key]],
                                        {18000: 0, 604800: 1}.get(q.window_seconds, 2)))
    snapshot.notes.append(tr('Antigravity 계정 한도입니다. Gemini 웹·앱, 개인 Claude·Codex 한도와 합산하지 않습니다.'))
    snapshot.notes.append(tr('공유 그룹은 한 번만 표시하며, 별도 Argon·Neon 한도는 CLI가 제공한 경우에만 표시합니다.'))
    return snapshot


def read_usage(executable, cancel, workspace, *, timeout=30, popen=subprocess.Popen):
    """A bounded, cancellable subprocess. Output is held in memory, never logged."""
    executable = validate_cli(executable)
    if cancel.is_set():
        raise ProviderError('CANCELLED')
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        process = popen([str(executable), '-p', '/usage', '--output-format', 'json',
                         '--disable-slash-commands=false', '--print-timeout', '25s',
                         '--log-file', os.devnull],
                        cwd=str(workspace), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError:
        raise ProviderError('AGY_COMMAND_FAILED') from None
    chunks, total = [], 0
    overflow, done, read_failed = threading.Event(), threading.Event(), threading.Event()

    def read():
        nonlocal total
        try:
            while chunk := process.stdout.read(65536):
                total += len(chunk)
                if total > OUTPUT_LIMIT:
                    overflow.set()
                    break
                chunks.append(chunk)
        except OSError:
            read_failed.set()
        finally:
            done.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        while process.poll() is None:
            if cancel.wait(.05):
                raise ProviderError('CANCELLED')
            if overflow.is_set():
                raise ProviderError('PARSE_ERROR')
            if time.monotonic() >= deadline:
                raise ProviderError('TIMEOUT')
        reader.join(1)
        if cancel.is_set():
            raise ProviderError('CANCELLED')
        if overflow.is_set() or read_failed.is_set() or not done.is_set():
            raise ProviderError('PARSE_ERROR')
        try:
            body = json.loads(b''.join(chunks))
        except (ValueError, UnicodeError):
            raise ProviderError('AGY_COMMAND_FAILED' if process.returncode else 'PARSE_ERROR') from None
        if process.returncode and isinstance(body, dict) and body.get('status') == 'SUCCESS':
            raise ProviderError('AGY_COMMAND_FAILED')
        return body
    finally:
        if process.poll() is None:
            process.kill()  # Only the quota subprocess created above.
        process.wait(timeout=3)
        reader.join(1)
        if done.is_set():
            process.stdout.close()


class AntigravitySession:
    def __init__(self, vault, directory, runner=read_usage):
        self.vault, self.directory, self.runner = vault, directory, runner
        self.tokens = None  # Link preference only; official CLI owns authentication.
        self.cli_path = None
        self.epoch = 0
        self.lock = threading.RLock()
        self.cancel = threading.Event()

    def check(self, epoch):
        if epoch != self.epoch or self.cancel.is_set():
            raise ProviderError('CANCELLED')

    def reset(self, delete=False):
        with self.lock:
            self.cancel.set()
            self.cancel = threading.Event()
            self.epoch += 1
            self.tokens = self.cli_path = None
            if delete:
                self.vault.delete('antigravity')
            return self.epoch

    def restore(self, epoch):
        with self.lock:
            self.check(epoch)
            saved = self.vault.load('antigravity')
            if not saved or saved.get('cli_link') is not True:
                return False
            value = saved.get('cli_path')
            if not isinstance(value, str):
                raise ProviderError('STORAGE_ERROR')
            self.cli_path, self.tokens = Path(value), True
            return True

    def connect(self, epoch, path):
        path = validate_cli(path)
        with self.lock:
            self.check(epoch)
            self.vault.save('antigravity', {'cli_link': True, 'cli_path': str(path)})
            self.cli_path, self.tokens = path, True
        return self.fetch(epoch)

    def fetch(self, epoch):
        with self.lock:
            self.check(epoch)
            path, cancel = self.cli_path, self.cancel
            if self.tokens is None:
                raise ProviderError('AUTH_REQUIRED')
        body = self.runner(path, cancel, self.directory / 'antigravity-workspace')
        value = normalize_antigravity(body)
        with self.lock:
            self.check(epoch)
        return value
