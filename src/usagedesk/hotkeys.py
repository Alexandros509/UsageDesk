"""Optional Windows hotkeys with transactional registration and local persistence."""

import ctypes
import json
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QIODevice, QSaveFile, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ACTIONS = {"toggle": "바 표시·숨기기", "usage": "사용량 창 열기",
           "programs": "프로그램 목록 열기", "refresh": "사용량 새로고침"}


def parse_key(text):
    if not text:
        return None
    sequence = QKeySequence(text, QKeySequence.PortableText)
    if sequence.count() != 1:
        raise ValueError("연속 입력 대신 하나의 키 조합을 지정하세요.")
    key = sequence[0]
    mods = key.keyboardModifiers()
    value = key.key().value
    if mods & (Qt.MetaModifier | Qt.KeypadModifier) or not mods & (Qt.ControlModifier | Qt.AltModifier):
        raise ValueError("Ctrl 또는 Alt를 포함하세요. Windows 키는 사용할 수 없습니다.")
    if 65 <= value <= 90 or 48 <= value <= 57:
        vk = value
    elif Qt.Key_F1.value <= value <= Qt.Key_F11.value:
        vk = 0x70 + value - Qt.Key_F1.value
    else:
        raise ValueError("문자 A–Z, 숫자 0–9 또는 F1–F11을 사용하세요.")
    modifiers = (2 if mods & Qt.ControlModifier else 0) | (1 if mods & Qt.AltModifier else 0)
    modifiers |= 4 if mods & Qt.ShiftModifier else 0
    return modifiers, vk


class WindowsKeys:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user.RegisterHotKey.restype = wintypes.BOOL
        self.user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.UnregisterHotKey.restype = wintypes.BOOL

    def register(self, identifier, combo):
        return bool(self.user.RegisterHotKey(None, identifier, combo[0] | 0x4000, combo[1]))

    def unregister(self, identifier):
        self.user.UnregisterHotKey(None, identifier)


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, directory, callbacks, blocked=lambda: False, backend=None):
        super().__init__()
        self.path = directory / "hotkeys.json"
        self.callbacks, self.blocked = callbacks, blocked
        self.backend = backend or WindowsKeys()
        self.bindings = {}
        self.config = {"enabled": False, "keys": dict.fromkeys(ACTIONS, "")}
        self.error = ""
        QApplication.instance().installNativeEventFilter(self)
        try:
            if self.path.exists():
                self.apply(json.loads(self.path.read_text("utf-8")), persist=False)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.error = f"단축키를 활성화하지 못했습니다: {exc}"

    def write(self, config):
        data = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.WriteOnly) or file.write(data) != len(data) or not file.commit():
            raise OSError("단축키 설정을 저장하지 못했습니다.")

    def apply(self, config, persist=True):
        if not isinstance(config, dict) or type(config.get("enabled")) is not bool:
            raise ValueError("잘못된 단축키 설정입니다.")
        keys = config.get("keys", {})
        if not isinstance(keys, dict):
            raise ValueError("잘못된 단축키 목록입니다.")
        normalized, wanted = {}, {}
        for action in ACTIONS:
            text = keys.get(action, "")
            if not isinstance(text, str):
                raise ValueError("잘못된 키 조합입니다.")
            combo = parse_key(text)
            normalized[action] = QKeySequence(text).toString(QKeySequence.PortableText) if text else ""
            if combo:
                if combo in wanted:
                    raise ValueError("같은 키 조합을 두 기능에 지정할 수 없습니다.")
                wanted[combo] = action
        clean = {"enabled": config["enabled"], "keys": normalized}
        if not clean["enabled"]:
            wanted = {}
        added = {}
        try:
            for combo in wanted:
                if combo in self.bindings:
                    continue
                occupied = {v[0] for v in (*self.bindings.values(), *added.values())}
                identifier = next(i for i in range(0x6000, 0xBFFF) if i not in occupied)
                if not self.backend.register(identifier, combo):
                    raise ValueError("이미 사용 중이거나 등록할 수 없는 조합입니다. 기존 설정을 유지합니다.")
                added[combo] = (identifier, wanted[combo])
            if persist:
                self.write(clean)
        except (OSError, ValueError):
            for identifier, _ in added.values():
                self.backend.unregister(identifier)
            if not persist:
                self.config = clean
            raise
        for combo, (identifier, _) in self.bindings.items():
            if combo not in wanted:
                self.backend.unregister(identifier)
        all_bindings = self.bindings | added
        self.bindings = {c: (all_bindings[c][0], a) for c, a in wanted.items()}
        self.config, self.error = clean, ""

    def dispatch(self, identifier):
        action = next((a for i, a in self.bindings.values() if i == identifier), None)
        if self.blocked():
            focus = QApplication.focusWidget()
            while focus is not None and not isinstance(focus, QKeySequenceEdit):
                focus = focus.parentWidget()
            if focus is not None and action:
                focus.setKeySequence(QKeySequence(self.config["keys"][action]))
            return
        if QApplication.activeModalWidget() is not None:
            return
        if action:
            self.callbacks[action]()

    def nativeEventFilter(self, event_type, message):
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message == 0x0312 and msg.wParam in {v[0] for v in self.bindings.values()}:
            self.dispatch(msg.wParam)
            return True, 0
        return False, 0

    def close(self):
        for identifier, _ in self.bindings.values():
            self.backend.unregister(identifier)
        self.bindings.clear()
        QApplication.instance().removeNativeEventFilter(self)


class HotkeyPage(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        layout = QVBoxLayout(self)
        description = QLabel("입력칸을 클릭하고 Ctrl/Alt + 문자·숫자·F1–F11을 누르세요.\n"
                             "이 설정 창에서 입력하는 동안 단축키 실행이 중지됩니다. 저장해야 적용됩니다.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.enabled = QCheckBox("전역 단축키 사용")
        self.enabled.setChecked(manager.config["enabled"])
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.edits = {}
        for action, title in ACTIONS.items():
            edit = QKeySequenceEdit(QKeySequence(manager.config["keys"][action]))
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            self.edits[action] = edit
            row = QHBoxLayout()
            row.addWidget(edit)
            clear = QPushButton("지우기")
            clear.clicked.connect(edit.clear)
            row.addWidget(clear)
            form.addRow(title, row)
        layout.addLayout(form)
        self.status = QLabel(manager.error or "기본값: 모두 미지정")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        save = QPushButton("저장")
        save.clicked.connect(self.save)
        reset = QPushButton("기본값 복원 · 모두 지우기")
        reset.clicked.connect(self.reset)
        row.addWidget(save)
        row.addWidget(reset)
        layout.addLayout(row)
        layout.addStretch()

    def reset(self):
        self.enabled.setChecked(False)
        for edit in self.edits.values():
            edit.clear()
        self.status.setText("저장을 누르면 모든 단축키가 해제됩니다.")

    def save(self):
        try:
            self.manager.apply({"enabled": self.enabled.isChecked(), "keys": {
                action: edit.keySequence().toString(QKeySequence.PortableText)
                for action, edit in self.edits.items()}})
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
        else:
            self.status.setText("저장했습니다. 설정 화면을 벗어나면 적용됩니다.")
