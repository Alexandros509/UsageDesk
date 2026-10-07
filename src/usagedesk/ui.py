from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .bar import UsageBar
from .hotkeys import HotkeyPage, Hotkeys
from .launcher import (
    BATCH,
    FILE_FILTER,
    MODES,
    Launcher,
    LauncherEntry,
    uses_file_association,
)
from .storage import ConfigError, ConfigStore
from .usage_ui import UsagePane


class EntryDialog(QDialog):
    def __init__(self, parent, entry: LauncherEntry | None = None, path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("프로그램 수정" if entry else "프로그램 추가")
        self.resize(620, 470)
        self.entry = entry
        self.result_entry = None
        self.name = QLineEdit(entry.name if entry else Path(path).stem)
        self.script = QLineEdit(entry.script_path if entry else path)
        self.cwd = QLineEdit(
            entry.working_directory if entry else str(Path(path).parent) if path else ""
        )
        self.args = QTextEdit()
        self.args.setPlainText("\n".join(entry.arguments) if entry else "")
        self.args.setPlaceholderText("한 행에 인수 하나 · 빈 행은 생략됩니다")
        self.args.setMaximumHeight(90)
        self.mode = QComboBox()
        for key, label in MODES.items():
            self.mode.addItem(label, key)
        if entry:
            self.mode.setCurrentIndex(self.mode.findData(entry.console_mode))
        self.favorite = QCheckBox("즐겨찾기")
        self.multiple = QCheckBox("중복 실행 허용 · 추적 가능한 프로세스에 적용")
        self.favorite.setChecked(entry.favorite if entry else False)
        self.multiple.setChecked(entry.allow_multiple if entry else False)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("이름", self.name)
        form.addRow("프로그램 / 파일", self.path_row(self.script, self.choose_script))
        form.addRow("작업 폴더", self.path_row(self.cwd, self.choose_directory))
        form.addRow("인수", self.args)
        form.addRow("콘솔", self.mode)
        form.addRow(self.favorite)
        form.addRow(self.multiple)
        layout.addLayout(form)
        note = QLabel(
            "Python은 작업 폴더의 .venv를 우선 사용합니다. 실행기가 설치되어 있어야 합니다.\n"
            "HTML·문서·미디어는 Windows 기본 앱으로 엽니다. 콘솔 유지는 CMD·BAT·PowerShell에서 지원합니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        test = QPushButton("현재 입력으로 시험 실행")
        test.clicked.connect(self.test_run)
        layout.addWidget(test)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.script.textChanged.connect(self.update_modes)
        self.update_modes()

    def update_modes(self):
        suffix = Path(self.script.text()).suffix.lower()
        for index in range(self.mode.count()):
            mode = self.mode.itemData(index)
            enabled = (mode == "close_on_exit" or
                       mode == "keep_open" and suffix in BATCH | {".ps1"} or
                       mode == "hidden" and not uses_file_association(self.script.text()))
            self.mode.model().item(index).setEnabled(enabled)
        if not self.mode.model().item(self.mode.currentIndex()).isEnabled():
            self.mode.setCurrentIndex(0)
        self.mode.setItemText(0, "기본 앱으로 열기" if uses_file_association(self.script.text())
                              else MODES["close_on_exit"])
        accepts_args = not uses_file_association(self.script.text()) or suffix in {".lnk", ".ahk"}
        self.args.setEnabled(accepts_args)

    def path_row(self, edit, callback):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit)
        button = QPushButton("찾기…")
        button.clicked.connect(callback)
        row.addWidget(button)
        return widget

    def choose_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "프로그램 또는 파일 선택", "", FILE_FILTER)
        if path:
            self.script.setText(str(Path(path)))
            self.cwd.setText(str(Path(path).parent))
            if not self.name.text():
                self.name.setText(Path(path).stem)

    def choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, "작업 폴더 선택")
        if path:
            self.cwd.setText(str(Path(path)))

    def value(self):
        fields = dict(
            name=self.name.text().strip(),
            script_path=self.script.text(),
            working_directory=self.cwd.text(),
            arguments=tuple(s for s in self.args.toPlainText().splitlines() if s)
            if self.args.isEnabled() else (),
            console_mode=self.mode.currentData(),
            favorite=self.favorite.isChecked(),
            allow_multiple=self.multiple.isChecked(),
        )
        value = replace(self.entry, **fields) if self.entry else LauncherEntry(**fields)
        value.validate()
        return value

    def save(self):
        try:
            self.result_entry = self.value()
        except ValueError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        self.accept()

    def test_run(self):
        try:
            value = self.value()
            # Keep the same unsaved identity across repeated test clicks.
            if self.entry is None:
                self.entry = value
            self.parent().launch(value)
        except ValueError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))


class WorkerSignals(QObject):
    done = Signal(object)


class Worker(QRunnable):
    def __init__(self, kind, operation):
        super().__init__()
        self.kind = kind
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self):
        try:
            value = self.operation()
            self.signals.done.emit((self, self.kind, value, None))
        except Exception as exc:
            # No logs, stdout or credentials; only local file/validation errors in this build.
            self.signals.done.emit((self, self.kind, None, str(exc)))


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore, tray_enabled=True):
        super().__init__()
        self.store = store
        self.entries = []
        self.statuses = {}
        self.launcher = Launcher()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.pending = set()
        self.launch_pending = set()
        self.polling = False
        self.quitting = False
        self.tray_available = tray_enabled and QSystemTrayIcon.isSystemTrayAvailable()
        self.setWindowTitle(f"UsageDesk {__version__} · Claude / Codex / Grok 사용량")
        self.resize(850, 600)
        self.setMinimumSize(600, 430)
        self.setAcceptDrops(True)
        icon = QIcon(str(Path(__file__).parent / "assets" / "usagedesk.ico"))
        QApplication.instance().setWindowIcon(icon)
        self.setWindowIcon(icon)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.usage = UsagePane(self.store.directory, self)
        self.tabs.addTab(self.usage, "사용량")
        programs = QWidget()
        layout = QVBoxLayout(programs)
        self.banner = QLabel(
            "프로그램·HTML·문서·미디어 파일을 등록하거나 끌어 놓으세요. 등록만으로 열리지 않습니다."
        )
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self.edit())
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.edit_buttons = []
        for label, callback in [
            ("추가", self.add),
            ("수정", self.edit),
            ("삭제", self.remove),
            ("실행", self.run_selected),
            ("위로", lambda: self.move(-1)),
            ("아래로", lambda: self.move(1)),
            ("폴더 열기", self.open_folder),
        ]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            self.edit_buttons.append(button)
            row.addWidget(button)
        layout.addLayout(row)
        self.recover_button = QPushButton("마지막 정상 백업으로 복구")
        self.recover_button.clicked.connect(self.recover)
        layout.addWidget(self.recover_button)
        self.tabs.addTab(programs, "내 프로그램")
        self.tabs.setCurrentIndex(0)
        self.bar = UsageBar(self, self.usage, self.store.directory)
        bar_button = QPushButton("사용량 바 표시")
        bar_button.clicked.connect(self.bar.restore_bar)
        self.tabs.setCornerWidget(bar_button)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("UsageDesk · Claude / Codex / Grok · 클릭하여 사용량 확인")
        self.tray.activated.connect(self.tray_activated)
        try:
            self.entries = self.store.load()
        except (OSError, ConfigError) as exc:
            self.banner.setText(
                f"설정 읽기 실패: {exc}\n변경·실행을 잠갔습니다. 원본은 보존되어 있습니다."
            )
        self.hotkeys = Hotkeys(self.store.directory, {
            "toggle": self.toggle_bar,
            "usage": lambda: self.open_tab(0),
            "programs": self.show_program_menu,
            "refresh": self.usage.refresh_all,
        }, blocked=self.editing_hotkeys)
        self.hotkey_page = HotkeyPage(self.hotkeys)
        self.tabs.addTab(self.hotkey_page, "단축키 설정")
        QApplication.instance().aboutToQuit.connect(self.hotkeys.close)
        self.destroyed.connect(self.hotkeys.close)
        self.render()
        if self.tray_available:
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.statusBar().showMessage(self.hotkeys.error or "준비 · 계정 없이 프로그램을 실행할 수 있습니다.")

    def editing_hotkeys(self):
        return self.isActiveWindow() and self.tabs.currentIndex() == 2

    def toggle_bar(self):
        if self.bar.isVisible():
            self.bar.tray_only()
        else:
            self.bar.restore_bar()

    def show_program_menu(self):
        from PySide6.QtGui import QCursor
        previous = getattr(self, "hotkey_program_menu", None)
        if previous:
            previous.close()
            previous.deleteLater()
        self.hotkey_program_menu = QMenu(self)
        if not self.entries:
            self.hotkey_program_menu.addAction("프로그램 추가", self.add)
        for entry in self.entries:
            self.hotkey_program_menu.addAction(entry.name.replace("&", "&&"),
                                                lambda e=entry: self.launch(e))
        self.hotkey_program_menu.popup(QCursor.pos())

    def selected(self):
        item = self.list.currentItem()
        return next((e for e in self.entries if item and e.id == item.data(Qt.UserRole)), None)

    def render(self):
        self.bar.refresh()
        selected = self.selected()
        self.list.clear()
        for entry in self.entries:
            status = self.statuses.get(entry.id, "아직 실행하지 않음")
            item = QListWidgetItem(
                f"{'★ ' if entry.favorite else ''}{entry.name}\n{entry.script_path}\n{MODES[entry.console_mode]} · {status}"
            )
            item.setData(Qt.UserRole, entry.id)
            item.setToolTip(f"{entry.script_path}\n작업 폴더: {entry.working_directory}")
            self.list.addItem(item)
            if selected and entry.id == selected.id:
                self.list.setCurrentItem(item)
        for button in self.edit_buttons:
            button.setEnabled(not self.store.blocked)
        self.recover_button.setVisible(self.store.can_recover())
        menu = QMenu(self)
        menu.addAction("사용량 열기", lambda: self.open_tab(0))
        menu.addAction("사용량 새로고침", self.usage.refresh_all)
        sub = menu.addMenu("내 프로그램")
        if not self.entries:
            sub.addAction("프로그램 추가", self.add).setEnabled(not self.store.blocked)
        for entry in sorted(self.entries, key=lambda e: not e.favorite):
            sub.addAction(entry.name.replace("&", "&&"), lambda e=entry: self.launch(e)).setEnabled(
                not self.store.blocked
            )
        menu.addAction("프로그램 관리", lambda: self.open_tab(1))
        menu.addSeparator()
        menu.addAction("사용량 바 표시", self.bar.restore_bar)
        menu.addAction("트레이 전용으로 숨기기", self.bar.tray_only)
        menu.addAction("바 표시 설정", self.bar.settings_dialog)
        menu.addAction("단축키 설정", lambda: self.open_tab(2))
        menu.addAction("바 위치 초기화", self.bar.reset_position)
        menu.addAction(
            "설정 폴더 열기",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory))),
        )
        menu.addSeparator()
        menu.addAction("정보 및 라이선스", self.show_licenses)
        menu.addAction("종료", self.quit)
        previous = getattr(self, "tray_menu", None)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        if previous:
            previous.deleteLater()

    def show_licenses(self):
        root = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parents[2])
        box = QMessageBox(self)
        box.setWindowTitle("UsageDesk · 라이선스")
        box.setText("UsageDesk: MIT\nQt / PySide6 / shiboken6: LGPL-3.0\n"
                    "포함 라이브러리의 원본 고지와 소스 제공·교체 안내를 확인하세요.")
        button = box.addButton("배포 고지 열기", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() == button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root / "DISTRIBUTION.md")))

    def commit(self, entries):
        try:
            self.store.save(entries)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "저장 실패", f"기존 목록을 유지합니다.\n{exc}")
            self.render()
            return False
        self.entries = entries
        self.render()
        return True

    def add(self, checked=False, path=""):
        if self.store.blocked:
            return
        if len(self.entries) >= 100:
            QMessageBox.warning(self, "등록 제한", "프로그램은 최대 100개입니다.")
            return
        dialog = EntryDialog(self, path=path)
        if dialog.exec() == QDialog.Accepted:
            self.commit([*self.entries, dialog.result_entry])

    def edit(self):
        entry = self.selected()
        if entry is None or self.store.blocked:
            return
        dialog = EntryDialog(self, entry)
        if dialog.exec() == QDialog.Accepted:
            self.commit([dialog.result_entry if e.id == entry.id else e for e in self.entries])

    def remove(self):
        entry = self.selected()
        if (
            entry
            and QMessageBox.question(
                self, "등록 삭제", "등록 정보를 삭제할까요? 실행 파일은 유지됩니다."
            )
            == QMessageBox.Yes
        ):
            self.commit([e for e in self.entries if e.id != entry.id])

    def move(self, direction):
        entry = self.selected()
        if not entry:
            return
        index = self.entries.index(entry)
        other = index + direction
        if 0 <= other < len(self.entries):
            entries = list(self.entries)
            entries[index], entries[other] = entries[other], entries[index]
            self.commit(entries)

    def open_folder(self):
        if entry := self.selected():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(entry.script_path).parent)))

    def recover(self):
        if (
            QMessageBox.question(
                self, "설정 복구", "손상 원본을 별도로 보존하고 마지막 정상 백업을 복구할까요?"
            )
            != QMessageBox.Yes
        ):
            return
        try:
            self.entries = self.store.recover()
            self.banner.setText(
                "백업 복구 완료. 손상 원본은 설정 폴더의 .corrupt 파일로 보존했습니다."
            )
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "복구 실패", str(exc))
        self.render()

    def run_selected(self):
        if entry := self.selected():
            self.launch(entry)

    def submit(self, kind, operation):
        worker = Worker(kind, operation)
        self.pending.add(worker)
        worker.signals.done.connect(self.completed)
        self.pool.start(worker)

    def launch(self, entry):
        if self.store.blocked or self.quitting:
            return
        if entry.id in self.launch_pending:
            self.statusBar().showMessage("같은 항목의 실행 요청을 처리 중입니다.")
            return
        self.launch_pending.add(entry.id)
        # No file checks or process creation on the GUI thread.
        self.statusBar().showMessage("실행 요청 확인 중…")
        self.submit(("launch", entry.id), lambda: self.launcher.start(entry))

    def poll(self):
        if self.polling or self.quitting:
            return
        self.polling = True
        self.submit(("poll", None), self.launcher.poll)

    @Slot(object)
    def completed(self, result):
        worker, (kind, entry_id), value, error = result
        self.pending.discard(worker)
        if self.quitting:
            return
        if kind == "launch":
            self.launch_pending.discard(entry_id)
        if kind == "poll":
            self.polling = False
            if not error and value != self.statuses:
                self.statuses.update(value)
                self.render()
        elif error:
            self.statusBar().showMessage("실행 실패 · 경로와 콘솔 설정을 확인하세요.")
            QMessageBox.warning(self, "실행 실패", error)
        else:
            self.statuses[entry_id] = ("실행 요청 완료 · 프로세스 추적 중" if value is not None
                                       else "파일 연결로 실행 요청 완료 · 프로세스 추적 불가")
            self.statusBar().showMessage(
                f"실행 요청 완료 · PID {value}" if value is not None
                else "Windows 파일 연결로 실행 요청 완료 · 실행 상태와 중복 여부는 추적할 수 없습니다."
            )
            self.render()

    def open_tab(self, index):
        self.tabs.setCurrentIndex(index)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.bar.options.tray_mode:
                self.open_tab(0)
            else:
                self.bar.show_bar()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.open_tab(0)

    def closeEvent(self, event):
        if not self.quitting:
            self.bar.show_bar()
            self.hide()
            event.ignore()
        else:
            self.quit()
            event.accept()

    def quit(self):
        self.hotkeys.close()
        self.quitting = True
        self.bar.hide()
        self.usage.shutdown()
        self.timer.stop()
        self.pool.clear()
        self.tray.hide()
        QApplication.instance().quit()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if (
            not self.store.blocked
            and len(urls) == 1
            and urls[0].isLocalFile()
            and Path(urls[0].toLocalFile()).is_file()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if (not self.store.blocked and len(urls) == 1 and urls[0].isLocalFile()
                and Path(urls[0].toLocalFile()).is_file()):
            self.add(path=str(Path(urls[0].toLocalFile())))
