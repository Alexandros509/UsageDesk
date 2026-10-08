from __future__ import annotations

import random
import subprocess
import time
from datetime import UTC, datetime

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .credentials import CredentialVault
from .domain import RefreshPolicy
from .grok import GROK_CLI, GrokSession
from .i18n import tr
from .oauth import CONFIGS, MANUAL_REDIRECT, LoopbackLogin, ProviderError, Transaction
from .providers import AccountSession

MESSAGES = {
    "AUTH_REQUIRED": tr('인증이 만료되었습니다. 다시 로그인하세요.'),
    "AUTH_DENIED": tr('로그인 승인이 취소되었습니다.'),
    "AUTH_EXPIRED": tr('로그인 시간이 만료되었습니다. 다시 시작하세요.'),
    "INVALID_CALLBACK": tr('콜백 주소·state가 일치하지 않습니다. 이번 로그인에서 받은 값을 붙여 넣으세요.'),
    "PORT_BUSY": tr('로그인 포트를 사용 중입니다. 다른 로그인 창을 닫고 다시 시도하세요.'),
    "RATE_LIMITED": tr('요청 제한 · 서버 대기 시간 후 재시도합니다.'),
    "OFFLINE": tr('네트워크 연결을 확인하세요.'),
    "TIMEOUT": tr('응답 시간이 초과되었습니다.'),
    "SERVER_ERROR": tr('서비스 오류 · 잠시 후 재시도합니다.'),
    "BLOCKED": tr('서비스에서 요청을 차단했습니다. 브라우저에서 계정을 확인한 후 다시 연결하세요.'),
    "PARSE_ERROR": tr('응답 형식이 예상과 다릅니다. 마지막 성공 데이터를 유지합니다.'),
    "UNSUPPORTED": tr('계정에서 표시할 사용량 데이터를 제공하지 않았습니다.'),
    "USAGE_UNAVAILABLE": tr('Grok 응답에 현재 사용량 값이 없습니다. 이전 값은 마지막 성공 조회 기준입니다. 새로고침으로 다시 확인할 수 있습니다.'),
    "REQUEST_REJECTED": tr('서비스가 요청을 거절했습니다. 인증 방식·계정 권한 확인이 필요합니다.'),
    "STORAGE_ERROR": tr('자격 증명 암호화·읽기·삭제에 실패했습니다. 다시 연결하세요. 삭제 실패 시 저장 파일이 남을 수 있습니다.'),
    "INTERNAL_ERROR": tr('처리에 실패했습니다. 다시 시도하세요.'),
}


def label(text=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    return widget


class LoginDialog(QDialog):
    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.config = CONFIGS[key]
        self.transaction = None
        self.listener = None
        self.code = None
        self.setWindowTitle(tr('{p0} 로그인', p0=self.config.label))
        self.resize(610, 290)
        layout = QVBoxLayout(self)
        layout.addWidget(
            label(
                tr('기본 브라우저에서 직접 로그인합니다. 비밀번호는 앱에 입력하지 않습니다.\n원본 공개 클라이언트를 참고한 실험적 연동입니다. 승인 화면의 앱과 권한을 확인하세요.')
            )
        )
        self.status = label(tr('브라우저 로그인을 시작하세요. 로그인은 5분 후 만료됩니다.'))
        layout.addWidget(self.status)
        self.start_button = QPushButton(tr('브라우저 로그인 시작'))
        self.start_button.clicked.connect(lambda: self.start(False))
        layout.addWidget(self.start_button)
        if key == "claude":
            manual = QPushButton(tr('Claude 대체 로그인 · 인증 코드 붙여 넣기'))
            manual.clicked.connect(lambda: self.start(True))
            layout.addWidget(manual)
        self.callback = QLineEdit()
        self.callback.setEchoMode(QLineEdit.Password)
        self.callback.setPlaceholderText(
            tr('자동 연결 실패 시 전체 콜백 URL · 대체 로그인에서는 code#state')
        )
        self.callback.setMaxLength(8192)
        layout.addWidget(self.callback)
        submit = QPushButton(tr('붙여 넣은 값 확인'))
        submit.clicked.connect(self.submit_callback)
        layout.addWidget(submit)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText(tr('취소'))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_callback)
        self.timer.start(200)
        self.finished.connect(self.cleanup)

    def start(self, manual=False):
        if self.listener:
            self.listener.close()
            self.listener = None
        if self.transaction:
            self.transaction.consumed = True
        self.callback.clear()
        try:
            if manual:
                self.transaction = Transaction(self.config, MANUAL_REDIRECT)
            else:
                self.listener = LoopbackLogin(self.config)
                self.transaction = self.listener.transaction
            if not QDesktopServices.openUrl(QUrl(self.transaction.authorize_url())):
                raise ProviderError("INTERNAL_ERROR")
            self.status.setText(
                tr('브라우저에서 승인한 후 돌아오세요. 자동 연결을 기다리는 중입니다.')
                if not manual
                else tr('브라우저에 표시된 code#state 또는 전체 콜백 URL을 아래에 붙여 넣으세요.')
            )
        except ProviderError as exc:
            self.status.setText(MESSAGES[exc.code])

    def submit_callback(self):
        if not self.transaction:
            return
        raw = self.callback.text().strip()
        self.callback.clear()
        try:
            self.code = self.transaction.accept(raw)
            self.accept()
        except ProviderError as exc:
            self.status.setText(MESSAGES[exc.code])

    def check_callback(self):
        if self.listener and self.listener.code:
            self.code = self.listener.code
            self.accept()
        elif self.listener and self.listener.error:
            self.status.setText(MESSAGES.get(self.listener.error, tr('로그인 실패')))
        elif self.transaction and self.transaction.expired:
            self.status.setText(MESSAGES["AUTH_EXPIRED"])
            if self.listener:
                self.listener.close()

    def cleanup(self, *_):
        self.timer.stop()
        self.callback.clear()
        if self.listener:
            self.listener.close()
        elif self.transaction:
            self.transaction.consumed = True


class Signals(QObject):
    done = Signal(object)


class Task(QRunnable):
    def __init__(self, key, epoch, kind, operation):
        super().__init__()
        self.key, self.epoch, self.kind, self.operation = key, epoch, kind, operation
        self.signals = Signals()

    def run(self):
        try:
            result, error = self.operation(), None
        except ProviderError as exc:
            result, error = None, exc
        except Exception:
            result, error = None, ProviderError("INTERNAL_ERROR")
        self.operation = None  # Drop closures holding authentication codes/verifiers.
        self.signals.done.emit((self, result, error))


class GrokLoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Grok CLI 계정 연결'))
        self.resize(580, 240)
        layout = QVBoxLayout(self)
        layout.addWidget(label(
            tr('설치된 Grok CLI의 현재 개인 계정으로 실제 사용 한도를 조회합니다.\nCLI 인증 토큰을 공식 Grok 한도 조회 서비스에 전송합니다.\n토큰은 별도 저장하지 않으며, 연결 해제는 CLI 로그인을 유지합니다.')
        ))
        layout.addWidget(label(str(GROK_CLI)))
        self.status = label(tr('이미 로그인했다면 ‘현재 CLI 계정 연결’을 누르세요.'))
        layout.addWidget(self.status)
        self.start_button = QPushButton(tr('Grok CLI 로그인 열기'))
        self.start_button.clicked.connect(self.start_login)
        layout.addWidget(self.start_button)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr('현재 CLI 계정 연결'))
        buttons.button(QDialogButtonBox.Cancel).setText(tr('취소'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.process = None

    def start_login(self):
        if self.process is not None and self.process.poll() is None:
            self.status.setText(tr('열린 CLI 창에서 로그인을 완료한 뒤 연결하세요.'))
            return
        try:
            self.process = subprocess.Popen(
                [str(GROK_CLI), "login", "--oauth"], cwd=str(GROK_CLI.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            self.status.setText(tr('CLI 창에서 로그인 완료 후 ‘현재 CLI 계정 연결’을 누르세요.'))
        except OSError:
            self.status.setText(tr('Grok CLI를 실행할 수 없습니다. 위 설치 경로를 확인하세요.'))


class UsagePane(QWidget):
    changed = Signal()

    def __init__(self, directory, parent=None, autoload=True):
        super().__init__(parent)
        self.sessions = {key: AccountSession(key, CredentialVault(directory)) for key in CONFIGS}
        self.sessions["grok"] = GrokSession(CredentialVault(directory))
        self.policies = {key: RefreshPolicy() for key in self.sessions}
        self.snapshots = {}
        self.errors = {}
        self.tasks = {}
        self.closed = False
        self.cards = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        layout = QVBoxLayout(self)
        layout.addWidget(
            label(
                tr('Claude · Codex · Grok 사용량 — 실험적 연동\nCodex는 ChatGPT 계정의 코딩 사용 한도입니다. Grok은 CLI 계정의 사용 한도를 조회합니다.')
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        cards_layout = QVBoxLayout(content)
        names = {key: config.label for key, config in CONFIGS.items()}
        names["grok"] = tr('Grok · CLI 계정')
        for key, name in names.items():
            card = QGroupBox(name)
            card_layout = QVBoxLayout(card)
            status = label(tr('계정 미연결'))
            card_layout.addWidget(status)
            data = QWidget()
            data_layout = QVBoxLayout(data)
            data_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.addWidget(data)
            row = QHBoxLayout()
            buttons = []
            # QPushButton.clicked emits a bool; keep it separate from the captured provider key.
            for text, callback in (
                (tr('로그인 / 다시 연결'), lambda checked=False, k=key: self.login(k)),
                (tr('새로고침'), lambda checked=False, k=key: self.refresh(k, True)),
                (tr('연결 해제'), lambda checked=False, k=key: self.disconnect(k)),
            ):
                button = QPushButton(text)
                button.clicked.connect(callback)
                row.addWidget(button)
                buttons.append(button)
            card_layout.addLayout(row)
            self.cards[key] = (status, data_layout, buttons)
            cards_layout.addWidget(card)
        cards_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        layout.addWidget(
            label(
                tr('Claude·Codex 인증은 Windows 사용자 범위로 암호화됩니다. Grok 인증은 설치된 CLI가 관리하며, 조회할 때만 읽습니다.')
            )
        )
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        if autoload:
            for key in self.sessions:
                self.restore(key)
        self.render()

    def submit(self, key, kind, operation):
        if key in self.tasks or self.closed:
            return
        task = Task(key, self.sessions[key].epoch, kind, operation)
        self.tasks[key] = task
        task.signals.done.connect(self.completed)
        self.pool.start(task)
        self.render()

    def restore(self, key):
        session = self.sessions[key]
        epoch = session.epoch
        self.submit(key, "restore", lambda: session.restore(epoch))

    def login(self, key):
        if key in self.tasks or (self.errors.get(key) == "RATE_LIMITED" and
                                time.monotonic() < self.policies[key].retry_at):
            return
        if key == "grok":
            dialog = GrokLoginDialog(self)
            accepted = dialog.exec() == QDialog.Accepted
            dialog.deleteLater()
            if accepted:
                session = self.sessions[key]
                epoch = session.reset()
                self.snapshots.pop(key, None)
                self.errors.pop(key, None)
                self.policies[key].reconnect()
                self.policies[key].begin(time.monotonic(), True)
                self.submit(key, "fetch", lambda: session.connect(epoch))
            return
        self.policies[key].disconnect()
        session = self.sessions[key]
        epoch = session.reset()
        self.snapshots.pop(key, None)
        dialog = LoginDialog(key, self)
        if dialog.exec() != QDialog.Accepted:
            dialog.deleteLater()
            self.restore(key)
            return
        transaction, code = dialog.transaction, dialog.code
        dialog.transaction = dialog.code = None
        dialog.deleteLater()
        self.policies[key].reconnect()
        self.policies[key].begin(time.monotonic(), True)
        self.submit(key, "fetch", lambda: session.login(epoch, transaction, code))

    def disconnect(self, key):
        self.policies[key].disconnect()
        self.snapshots.pop(key, None)
        try:
            self.sessions[key].reset(delete=True)
            self.errors.pop(key, None)
        except ProviderError as exc:
            self.errors[key] = exc.code
        self.render()

    def refresh(self, key, manual=False):
        if key in self.tasks or self.sessions[key].tokens is None:
            return
        policy = self.policies[key]
        if policy.begin(time.monotonic(), manual) is None:
            return
        session = self.sessions[key]
        epoch = session.epoch
        self.submit(key, "fetch", lambda: session.fetch(epoch))

    def refresh_all(self):
        for key in self.sessions:
            self.refresh(key, True)

    def tick(self):
        if not self.closed:
            for key in self.sessions:
                self.refresh(key)
            self.render()

    @Slot(object)
    def completed(self, result):
        task, value, error = result
        if self.tasks.get(task.key) is task:
            self.tasks.pop(task.key)
        if self.closed or task.epoch != self.sessions[task.key].epoch:
            return
        key = task.key
        if task.kind == "restore":
            if error:
                self.errors[key] = error.code
            elif value:
                self.policies[key].reconnect()
                self.refresh(key)
        else:
            policy = self.policies[key]
            policy.finish(
                policy.generation,
                time.monotonic(),
                error.code if error else None,
                error.retry_after if error else None,
                random.uniform(0, 5),
            )
            if error:
                self.errors[key] = error.code
                if error.code in {"STORAGE_ERROR", "REQUEST_REJECTED"}:
                    policy.halted = True
            else:
                self.errors.pop(key, None)
                self.snapshots[key] = value
        self.render()

    def render(self):
        now = datetime.now(UTC)
        for key, (status, layout, buttons) in self.cards.items():
            policy = self.policies[key]
            snapshot = self.snapshots.get(key)
            error = self.errors.get(key)
            text = MESSAGES.get(
                error, tr('계정 미연결') if self.sessions[key].tokens is None else tr('연결됨')
            )
            if key == "grok" and error == "AUTH_REQUIRED":
                text = tr('Grok CLI 인증이 필요합니다. 로그인 / 다시 연결에서 CLI 로그인 후 연결하세요.')
            if key in self.tasks:
                text = tr('인증·사용량 확인 중…')
            if policy.retry_at > time.monotonic():
                text += tr(' · 재시도까지 {p0}초', p0=int(policy.retry_at - time.monotonic()) + 1)
            elif error == "USAGE_UNAVAILABLE" and policy.next_auto > time.monotonic():
                minutes = max(1, int((policy.next_auto - time.monotonic() + 59) // 60))
                text += tr(' · 다음 자동 조회 약 {p0}분 후', p0=minutes)
            if snapshot:
                age = (now - snapshot.fetched_at).total_seconds()
                stale = bool(error) or age > 360
                text += f"\n{tr('이전 데이터') if stale else tr('마지막 성공 조회')} · {snapshot.fetched_at.astimezone():%m-%d %H:%M:%S}"
            status.setText(text)
            buttons[0].setEnabled(key not in self.tasks and
                                  (error != "RATE_LIMITED" or time.monotonic() >= policy.retry_at))
            buttons[1].setEnabled(
                self.sessions[key].tokens is not None
                and not policy.halted
                and key not in self.tasks
                and time.monotonic() >= policy.retry_at
                and time.monotonic() - policy.last_start >= 10
            )
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if not snapshot:
                layout.addWidget(label("—"))
                continue
            for quota in snapshot.limits:
                percent = quota.used_percent
                layout.addWidget(
                    label(tr('{p0} · 사용 {p1:g}% · 남음 {p2:g}%', p0=quota.label, p1=percent, p2=quota.remaining_percent))
                )
                bar = QProgressBar()
                bar.setValue(round(min(percent, 100)))
                bar.setTextVisible(False)
                layout.addWidget(bar)
                if quota.reset_at_utc:
                    seconds = (quota.reset_at_utc - now).total_seconds()
                    remaining = (
                        tr('초기화 확인 대기')
                        if seconds <= 0
                        else tr('{p0}시간 {p1}분 남음', p0=int(seconds) // 3600, p1=int(seconds) % 3600 // 60)
                    )
                    layout.addWidget(
                        label(tr('초기화 {p0:%m-%d %H:%M} · {p1}', p0=quota.reset_at_utc.astimezone(), p1=remaining))
                    )
                else:
                    layout.addWidget(label(tr('초기화 시간 미제공')))
            for note in snapshot.notes + list(dict.fromkeys(snapshot.warnings)):
                layout.addWidget(label(note))
        self.changed.emit()

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        for session in self.sessions.values():
            session.reset()
        self.pool.clear()
