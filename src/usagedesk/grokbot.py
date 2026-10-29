"""Read-only Grok Bot weekly usage through the Cursor web dashboard protocol.

Protocol reference: bcharleson/grokbot-usage-cli, cli/grokbot_usage.py.
Only an explicit connection enables local Cursor credential reads. No browser
cookie database, conversation database, or Grok Bot vault is inspected.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .domain import QuotaLimit
from .i18n import tr
from .oauth import ProviderError
from .providers import Snapshot, Transport

USAGE_URL = 'https://cursor.com/api/dashboard/get-sand-usage-status'
DASHBOARD_URL = 'https://cursor.com/dashboard?tab=usage'
COOKIE_NAME = 'WorkosCursorSessionToken'
MAX_SESSION = 16384


def session_cookie(raw):
    """Validate one cookie or Cursor JWT; never accept a whole Cookie header."""
    if not isinstance(raw, str) or len(raw) > MAX_SESSION:
        raise ProviderError('INVALID_SESSION')
    raw = raw.strip()
    if raw.startswith(COOKIE_NAME + '='):
        raw = raw[len(COOKIE_NAME) + 1:]
    if not raw or any(ord(c) < 33 or ord(c) > 126 for c in raw):
        raise ProviderError('INVALID_SESSION')
    if '::' in raw:
        raw = raw.replace('::', '%3A%3A', 1)
    if '%3A%3A' in raw:
        subject, token = raw.split('%3A%3A', 1)
    else:
        token = raw
        try:
            part = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
            subject = claims['sub'].split('|')[-1]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, UnicodeError):
            raise ProviderError('INVALID_SESSION') from None
    if (not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', subject)
            or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token)):
        raise ProviderError('INVALID_SESSION')
    result = subject + '%3A%3A' + token
    if len(result) > MAX_SESSION:
        raise ProviderError('INVALID_SESSION')
    return result


def cursor_database():
    return Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming')) / 'Cursor' / 'User' / 'globalStorage' / 'state.vscdb'


def read_cursor_session(path):
    """Query only the access-token row in place, with SQLite read-only mode."""
    if not path.is_file():
        raise ProviderError('LOCAL_AUTH_MISSING')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.execute('PRAGMA query_only=ON')
            row = db.execute('SELECT substr(value, 1, ?) FROM ItemTable WHERE key=? LIMIT 1',
                             (MAX_SESSION + 1, 'cursorAuth/accessToken')).fetchone()
        if not row or not row[0]:
            raise ProviderError('LOCAL_AUTH_MISSING')
        return session_cookie(row[0])
    except (sqlite3.Error, OSError):
        raise ProviderError('STORAGE_ERROR') from None


def normalize_grokbot(body, now=None):
    if not isinstance(body, dict):
        raise ProviderError('PARSE_ERROR')
    percent = body.get('usagePercent')
    if percent is not None and (type(percent) not in (int, float)
                               or not math.isfinite(percent) or percent < 0):
        raise ProviderError('PARSE_ERROR')
    reset = body.get('nextResetTimestampUtc')
    moment = None
    if reset is not None:
        try:
            moment = datetime.fromisoformat(reset.replace('Z', '+00:00'))
            if moment.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise ProviderError('PARSE_ERROR') from None
    if percent is None and moment is None:
        raise ProviderError('USAGE_UNAVAILABLE')
    snapshot = Snapshot(now or datetime.now(UTC))
    title = tr('주간 한도')
    plan = body.get('grokPlanLabel')
    if isinstance(plan, str) and plan.strip():
        # Labels are display-only, plain text, bounded and stripped of controls.
        plan = ''.join(c for c in plan if c.isprintable())[:80].strip()
        if plan:
            snapshot.notes.append(tr('요금제: {p0}', p0=plan))
    if body.get('isUnified') is True:
        title = tr('통합 주간 한도')
        snapshot.notes.append(tr('공유 사용 한도입니다. 기존 Grok 게이지와 합산하지 않습니다.'))
    snapshot.limits.append(QuotaLimit('weekly', title, percent, moment, 604800))
    if percent is None:
        snapshot.warnings.append(tr('인증은 연결되어 있지만 서비스가 사용률을 제공하지 않았습니다. 초기화 시각만 표시합니다.'))
    if body.get('hasAvailableUsage') is False:
        snapshot.warnings.append(tr('서비스에서 현재 사용 가능 한도가 없다고 응답했습니다.'))
    if body.get('hasNonZeroIncludedLimit') is False:
        snapshot.warnings.append(tr('이 계정에는 포함된 Grok Bot 한도가 없습니다.'))
    snapshot.notes.append(tr('Grok Bot 웹 계정 한도 · 웹 세션이 만료되면 다시 연결하세요.'))
    return snapshot


class GrokBotSession:
    def __init__(self, vault, transport=None, db_path=None):
        self.vault = vault
        self.transport = transport or Transport()
        self.db_path = db_path or cursor_database()
        self.tokens = None  # Link state; secret values are never represented in the UI.
        self.source = None
        self._cookie = None
        self.epoch = 0
        self.lock = threading.RLock()
        self.cancel = threading.Event()

    def revision(self):
        if self.source != 'cursor':
            return None
        try:
            files = (self.db_path, Path(str(self.db_path) + '-wal'))
            return tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                         for p in files)
        except OSError:
            return None

    def check(self, epoch):
        if epoch != self.epoch or self.cancel.is_set():
            raise ProviderError('CANCELLED')

    def reset(self, delete=False):
        with self.lock:
            self.cancel.set()
            self.cancel = threading.Event()
            self.epoch += 1
            self.tokens = self.source = self._cookie = None
            if delete:
                self.vault.delete('grokbot')
            return self.epoch

    def restore(self, epoch):
        with self.lock:
            self.check(epoch)
            data = self.vault.load('grokbot')
            if data is None:
                return False
            source = data.get('source')
            if source not in ('cursor', 'session'):
                raise ProviderError('STORAGE_ERROR')
            cookie = session_cookie(data.get('session_cookie')) if source == 'session' else None
            self.source, self._cookie, self.tokens = source, cookie, True
            return True

    def connect(self, epoch, source, raw=''):
        if source not in ('cursor', 'session'):
            raise ProviderError('INVALID_SESSION')
        cookie = session_cookie(raw) if source == 'session' else None
        # Validate the selected local credential without copying its database.
        if source == 'cursor':
            read_cursor_session(self.db_path)
        with self.lock:
            self.check(epoch)
            data = {'source': source}
            if cookie:
                data['session_cookie'] = cookie
            self.vault.save('grokbot', data)
            self.source, self._cookie, self.tokens = source, cookie, True
        return self.fetch(epoch)

    def fetch(self, epoch):
        with self.lock:
            self.check(epoch)
            if self.tokens is None:
                raise ProviderError('AUTH_REQUIRED')
            source, cookie, cancel = self.source, self._cookie, self.cancel
        if source == 'cursor':
            cookie = read_cursor_session(self.db_path)
        with self.lock:
            self.check(epoch)
        body = self.transport.request('POST', USAGE_URL, json={}, cancel=cancel,
                                      headers={'Cookie': f'{COOKIE_NAME}={cookie}',
                                               'Origin': 'https://cursor.com'})
        result = normalize_grokbot(body)
        with self.lock:
            self.check(epoch)
        return result
