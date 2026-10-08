"""Explicit application language; setting changes take effect on a clean restart."""

import json

from PySide6.QtCore import QIODevice, QSaveFile, QTranslator

from .i18n_catalog import EN

_language = "ko"
_qt_translator = None
_appearance = "system"


def language():
    return _language


def tr(source, **values):
    text = EN.get(source, source) if _language == "en" else source
    return text.format(**values) if values else text


class StandardButtons(QTranslator):
    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        if context not in ("QPlatformTheme", "QDialogButtonBox", "QMessageBox"):
            return source
        labels = {"OK": "확인", "Save": "저장", "Cancel": "취소", "Close": "닫기",
                  "Yes": "예", "No": "아니요", "Apply": "적용", "Reset": "초기화",
                  "Open": "열기", "Help": "도움말", "Discard": "버리기"}
        return labels.get(source.replace("&", ""), source) if _language == "ko" else source


def initialize(directory, app=None):
    global _language, _qt_translator, _appearance
    try:
        value = json.loads((directory / "language.json").read_text("utf-8"))
        prefs = value if isinstance(value, dict) else {}
        value = prefs.get("language")
    except (OSError, ValueError):
        value, prefs = None, {}
    _appearance = prefs.get("appearance", "system")
    if _appearance not in ("system", "light", "dark"):
        _appearance = "system"
    _language = value if value in ("ko", "en") else "ko"
    if app is not None:
        if _qt_translator is not None:
            app.removeTranslator(_qt_translator)
            _qt_translator.deleteLater()
        _qt_translator = StandardButtons(app)
        app.installTranslator(_qt_translator)
        from .appearance import apply_theme
        apply_theme(app, _appearance)
        if not app.property("usagedesk_theme_hook"):
            app.styleHints().colorSchemeChanged.connect(
                lambda *_: apply_theme(app, _appearance) if _appearance == "system" else None)
            app.setProperty("usagedesk_theme_hook", True)
    return _language


def appearance():
    return _appearance


def save_language(directory, value, appearance=None):
    if value not in ("ko", "en"):
        raise ValueError("Unsupported language")
    try:
        prefs = json.loads((directory / "language.json").read_text("utf-8"))
        prefs = prefs if isinstance(prefs, dict) else {}
    except (OSError, ValueError):
        prefs = {}
    prefs = {key: prefs[key] for key in ("language", "appearance") if key in prefs}
    prefs["language"] = value
    if appearance is not None:
        if appearance not in ("system", "light", "dark"):
            raise ValueError("Unsupported appearance")
        prefs["appearance"] = appearance
    data = (json.dumps(prefs) + "\n").encode()
    file = QSaveFile(str(directory / "language.json"))
    if not file.open(QIODevice.WriteOnly) or file.write(data) != len(data) or not file.commit():
        raise OSError(tr("언어 설정을 저장할 수 없습니다."))
