"""Language preference with an explicit restart after successful persistence."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .i18n import appearance, initialize, language, save_language, tr


class LanguagePage(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.choice = QComboBox()
        self.choice.addItem("한국어", "ko")
        self.choice.addItem("English", "en")
        self.choice.setCurrentIndex(self.choice.findData(language()))
        form.addRow(tr("앱 언어"), self.choice)
        self.appearance = QComboBox()
        for title, key in [(tr("시스템"), "system"), (tr("밝게"), "light"), (tr("어둡게"), "dark")]:
            self.appearance.addItem(title, key)
        self.appearance.setCurrentIndex(self.appearance.findData(appearance()))
        form.addRow(tr("설정 창 테마"), self.appearance)
        layout.addLayout(form)
        note = QLabel(tr("적용을 누르면 테마는 즉시 반영되고, 언어를 변경한 경우 앱이 재시작됩니다. 저장된 계정·프로그램·배치·단축키 설정은 유지됩니다."))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.save = QPushButton(tr("적용"))
        self.save.clicked.connect(self.apply)
        layout.addWidget(self.save)
        layout.addStretch()

    def apply(self):
        try:
            save_language(self.owner.store.directory, self.choice.currentData(),
                          self.appearance.currentData())
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        if self.choice.currentData() == language():
            initialize(self.owner.store.directory, QApplication.instance())
            self.status.setText(tr("저장했습니다. 설정 창 테마를 적용했습니다."))
            return
        self.save.setEnabled(False)
        self.owner.restart_requested = True
        QTimer.singleShot(0, self.owner.quit)
