"""A compact Windows companion bar. It does not modify the Explorer taskbar."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QListView,
    QMenu,
    QMessageBox,
    QScrollArea,
    QStyledItemDelegate,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .appbar import TopDock
from .appearance import theme_color, themed_style
from .i18n import tr


def service_icon(provider):
    """Render the reference's familiar brand mark on a compact rounded tile."""
    filename = {"claude": "claude.svg", "codex": "openai.svg", "grok": "grok.svg"}[provider]
    svg = (Path(__file__).parent / "assets" / filename).read_bytes()
    renderer = QSvgRenderer(svg.replace(b"currentColor", b"#ffffff"))
    pixmap = QPixmap(96, 96)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor({"claude": "#d97757", "codex": "#233c3f", "grok": "#111111"}[provider]))
    painter.drawRoundedRect(QRectF(0, 0, 96, 96), 20, 20)
    renderer.render(painter, QRectF(14, 14, 68, 68))
    painter.end()
    return pixmap


@dataclass(frozen=True)
class DisplayOptions:
    size: str = "standard"
    theme: str = "translucent"
    appearance: str = "system"
    icons: bool = True
    percentages: bool = True
    mode: str = "smart"
    selected: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    length: str = "auto"
    placement: str = "floating"
    quota_display: str = "used"
    reset_display: str = "off"
    tray_mode: bool = False
    hide_unselected: bool = False
    cpu_ai: bool = False
    cpu_programs: bool = False


class DisplayStore:
    def __init__(self, directory):
        self.settings = QSettings(str(directory / "display.ini"), QSettings.IniFormat)

    def load(self):
        def choice(key, allowed, default):
            value = self.settings.value(key, default)
            return value if value in allowed else default

        def boolean(key, default="true"):
            return str(self.settings.value(key, default)).lower() == "true"

        icons, percentages = boolean("icons"), boolean("percentages")
        def selection(key):
            values = self.settings.value(key, [])
            if isinstance(values, str):
                values = [values]
            return tuple(v for v in values if isinstance(v, str)) if isinstance(values, list) else ()
        legacy_tray = self.settings.value("placement") == "tray"
        return DisplayOptions(
            choice("size", ("compact", "standard", "prominent"), "standard"),
            choice("theme", ("translucent", "background", "mono"), "translucent"),
            choice("appearance", ("system", "light", "dark"), "system"),
            icons,
            percentages or not icons,
            choice("mode", ("smart", "custom"), "smart"),
            selection("selected"),
            selection("programs"),
            choice("length", ("auto", "wide", "full"), "auto"),
            choice("placement", ("floating", "top", "bottom"), "floating"),
            choice("quota_display", ("used", "remaining"), "used"),
            choice("reset_display", ("off", "inline"), "off"),
            legacy_tray or boolean("tray_mode", "false"),
            boolean("hide_unselected", "false"),
            boolean("cpu_ai", "false"),
            boolean("cpu_programs", "false"),
        )

    def save(self, options):
        for key in ("size", "theme", "appearance", "icons", "percentages", "mode", "length",
                    "placement", "quota_display", "reset_display", "tray_mode", "hide_unselected", "cpu_ai", "cpu_programs"):
            self.settings.setValue(key, getattr(options, key))
        self.settings.setValue("selected", list(options.selected))
        self.settings.setValue("programs", list(options.programs))
        self.sync()

    def position(self):
        point = self.settings.value("position")
        return point if isinstance(point, QPoint) else None

    def save_position(self, point):
        self.settings.setValue("position", point)
        self.sync()

    def sync(self):
        self.settings.sync()
        if self.settings.status() != QSettings.NoError:
            raise OSError(tr('표시 설정을 저장할 수 없습니다.'))


def visible_limits(snapshot, provider, options):
    if not snapshot:
        return []
    return [
        quota for quota in snapshot.limits
        if quota.used_percent is not None
        and (options.mode == "smart" or quota_selected(snapshot, provider, quota.key, options.selected))
    ]


def quota_selected(snapshot, provider, key, selected):
    return f"{provider}/{key}" in selected or any(
        f"{provider}/{old}" in selected for old, new in snapshot.aliases.items() if new == key
    )


def short_label(quota):
    if quota.key.startswith(("weekly_scoped:", "seven_day_")):
        return quota.label.split(" · ")[0][:10]
    if quota.window_seconds:
        hours = quota.window_seconds / 3600
        return f"{hours / 24:g}d" if hours >= 24 else f"{hours:g}h"
    if "five_hour" in quota.key:
        return "5h"
    if quota.key == "seven_day":
        return "7d"
    return quota.label[:8]


def reset_countdown(quota, now=None):
    if quota.reset_at_utc is None:
        return tr('시간 미제공')
    seconds = (quota.reset_at_utc - (now or datetime.now(UTC))).total_seconds()
    if seconds <= 0:
        return tr('초기화 확인 중')
    minutes = int(seconds // 60)
    if minutes >= 7 * 1440:
        return f"{minutes // (7 * 1440)}wk {(minutes % (7 * 1440)) // 1440}d"
    if minutes >= 1440:
        return f"{minutes // 1440}d {(minutes % 1440) // 60}hr"
    if minutes >= 60:
        return f"{minutes // 60}hr {minutes % 60}min"
    return f"{minutes}min" if minutes else "<1min"


def exhausted_weekly(quota, provider, snapshot):
    """Account-wide weekly exhaustion limits the five-hour subscription pool."""
    if snapshot is None:
        return None
    if provider == "codex":
        if quota.window_seconds != 18000 or quota.used_percent is None:
            return None
        return next((q for q in snapshot.limits if q.window_seconds == 604800
                     and q.used_percent is not None and q.used_percent >= 100), None)
    if provider != "claude" or quota.key != "five_hour":
        return None
    return next((q for q in snapshot.limits if q.key == "seven_day"
                 and q.used_percent is not None and q.used_percent >= 100), None)


def quota_caption(quota, options, now=None, *, provider=None, snapshot=None):
    caption = short_label(quota)
    if exhausted_weekly(quota, provider, snapshot) is not None:
        suffix = tr(' · 추가 사용 활성') if snapshot.extra_usage_enabled else ""
        return caption + tr(' │ 주간 한도 소진') + suffix
    if options.reset_display == "inline":
        countdown = reset_countdown(quota, now).removeprefix(tr('초기화 '))
        caption += tr(' │ 초기화 ') + countdown
    return caption


def display_percent(quota, options, *, provider=None, snapshot=None):
    if exhausted_weekly(quota, provider, snapshot) is not None:
        return 0 if options.quota_display == "remaining" else 100
    return quota.remaining_percent if options.quota_display == "remaining" else quota.used_percent


def weekly_explanation(quota, provider, snapshot):
    weekly = exhausted_weekly(quota, provider, snapshot)
    if weekly is None:
        return ""
    text = (tr('\n5시간 한도 자체: 사용 {p0:g}% · 남음 {p1:g}%\n주간 한도 소진으로 구독 잔량 0% · 소진 상태 100%로 표시합니다.', p0=quota.used_percent, p1=quota.remaining_percent))
    if snapshot.extra_usage_enabled:
        text += tr('\n추가 사용 활성 · 추가 사용 가능 여부는 별도 한도와 결제 상태에 따릅니다.')
    else:
        text += tr('\n주간 한도로 인해 구독 사용이 제한됩니다.')
    if weekly.reset_at_utc:
        text += tr('\n주간 초기화 예정 {p0:%Y-%m-%d %H:%M %Z}', p0=weekly.reset_at_utc.astimezone())
    else:
        text += tr('\n주간 초기화 시간 미제공')
    return text


def program_label(name, metrics):
    """Balance the complete name on at most two lines without eliding it."""
    if metrics.horizontalAdvance(name) <= 80 or len(name) < 2:
        return name
    candidates = []
    for index in range(1, len(name)):
        first, second = name[:index].rstrip(), name[index:].lstrip()
        if not first or not second:
            continue
        width = max(metrics.horizontalAdvance(first), metrics.horizontalAdvance(second))
        natural = (name[index - 1] in " _-:/" or name[index] in " _-:/"
                   or name[index - 1].islower() and name[index].isupper())
        candidates.append((width + (0 if natural else 10), first + "\n" + second))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else name


def keep_on_screen(rect, available):
    """Clamp against a work area in Qt logical pixels, including negative origins."""
    return QPoint(
        max(available.left(), min(rect.x(), available.right() - rect.width() + 1)),
        max(available.top(), min(rect.y(), available.bottom() - rect.height() + 1)),
    )


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


def cpu_text(owner, key):
    value = getattr(owner, "cpu_values", {}).get(key)
    return f"CPU {value.percent:.1f}%" if value and value.percent is not None else "CPU —"


def cpu_tooltip(owner, key):
    value = getattr(owner, "cpu_values", {}).get(key)
    states = {
        "not_running": tr("실행 중인 로컬 AI 프로세스를 찾지 못했습니다."),
        "untracked": tr("프로세스를 특정할 수 없습니다. HTML·문서와 공유 브라우저는 파일별 CPU를 구분할 수 없습니다."),
        "sampling": tr("CPU 측정 중입니다. 다음 갱신을 기다려 주세요."),
        "unavailable": tr("프로세스 종료 또는 접근 제한으로 CPU를 측정할 수 없습니다."),
        "ready": tr("이 PC의 프로세스와 관측된 하위 프로세스 합계 · 전체 CPU 용량 기준 0–100% · 약 2초 간격"),
    }
    return "\n" + cpu_text(owner, key) + " · " + states.get(value.state if value else "sampling", states["unavailable"])


def configure_popup(combo):
    # The Windows 11 combo delegate can ignore a styled popup's text palette.
    # Use an ordinary item view/delegate with explicit colors in every system theme.
    view = combo.view() if combo.property("usagedesk_popup") else QListView(combo)
    if not combo.property("usagedesk_popup"):
        view.setItemDelegate(QStyledItemDelegate(view))
    palette = view.palette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for role, color in ((QPalette.Base, "#ffffff"), (QPalette.Text, "#303747"),
                            (QPalette.Highlight, "#e1edff"),
                            (QPalette.HighlightedText, "#143967")):
            palette.setColor(group, role, QColor(theme_color(color)))
    view.setPalette(palette)
    view.setStyleSheet("""
        QListView { color: #303747; background: #ffffff; border: 1px solid #d9dfe8;
            selection-color: #143967; selection-background-color: #e1edff; outline: 0; }
        QListView::item { min-height: 28px; padding: 3px 10px; }
        QListView::item:selected { color: #143967; background: #e1edff; }
        QListView::item:hover { color: #143967; background: #edf3ff; }
    """)
    view.setStyleSheet(themed_style(view.styleSheet()))
    if not combo.property("usagedesk_popup"):
        combo.setView(view)
        combo.setProperty("usagedesk_popup", True)


class DisplayDialog(QDialog):
    def __init__(self, parent, options, snapshots, programs=()):
        super().__init__(parent)
        self.setWindowTitle(tr('UsageDesk · 표시 설정'))
        self.resize(490, 570)
        palette = QPalette()
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            for role, color in (
                (QPalette.Window, "#f5f6f8"), (QPalette.WindowText, "#303747"),
                (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f0f3f8"),
                (QPalette.Text, "#303747"), (QPalette.Button, "#ffffff"),
                (QPalette.ButtonText, "#303747"), (QPalette.Highlight, "#dbeaff"),
                (QPalette.HighlightedText, "#163d71"), (QPalette.PlaceholderText, "#78869b"),
            ):
                palette.setColor(group, role, QColor(color))
        self.setPalette(palette)
        self.setStyleSheet("""
            QDialog { background: #f5f6f8; }
            QWidget#displayPage, QWidget#programPage { background: #f5f6f8; }
            QLabel, QCheckBox, QGroupBox { color: #303747; background: transparent; }
            QTabWidget::pane { background: #f5f6f8; border: 1px solid #dfe3ea; border-radius: 8px; }
            QTabBar::tab { background: #e7ebf2; color: #536177; padding: 8px 16px;
                border: 1px solid #dfe3ea; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: white; color: #173e71; }
            QTabBar::tab:hover { background: #edf3fc; color: #173e71; }
            QGroupBox { background: white; border: 1px solid #dfe3ea;
                border-radius: 10px; margin-top: 14px; padding: 16px 12px 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
            QComboBox { color: #303747; background: white; border: 1px solid #d9dfe8;
                border-radius: 5px; min-height: 26px; padding-left: 8px; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox::down-arrow { image: url("__CHEVRON__"); width: 12px; height: 12px; }
            QScrollArea { border: none; background: white; }
            QScrollArea > QWidget > QWidget { background: white; }
            QCheckBox::indicator { width: 16px; height: 16px; background: white;
                border: 1px solid #8e9cb1; border-radius: 4px; }
            QCheckBox::indicator:checked { background: #2563eb; border-color: #2563eb;
                image: url("__CHECK_MARK__"); }
            QCheckBox::indicator:disabled { background: #e8edf4; border-color: #c7d0df; }
            QCheckBox:disabled { color: #778397; }
            QPushButton { min-height: 28px; padding: 0 16px; background: white;
                color: #303747; border: 1px solid #cbd4e1; border-radius: 6px; }
            QPushButton:hover { background: #edf3fc; border-color: #82a7dc; }
            QPushButton:pressed { background: #dce9fb; }
            QPushButton:focus { border: 2px solid #2563eb; }
            QPushButton#saveDisplay { background: #2563eb; color: white; border-color: #2563eb; }
            QPushButton#saveDisplay:hover { background: #1d4ed8; }
        """.replace("__CHECK_MARK__", (Path(__file__).parent / "assets" / "check.svg").as_posix())
            .replace("__CHEVRON__", (Path(__file__).parent / "assets" / "chevron.svg").as_posix()))
        self._light_style = self.styleSheet()
        self._light_palette = QPalette(self.palette())
        self.refresh_theme()
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        display_page = QWidget()
        display_page.setObjectName("displayPage")
        self.display_scroll = QScrollArea()
        self.display_scroll.setWidgetResizable(True)
        self.display_scroll.setWidget(display_page)
        self.tabs.addTab(self.display_scroll, tr('표시'))
        layout = QVBoxLayout(display_page)
        intro = QLabel(tr('메뉴 바처럼 사용량을 항상 표시합니다.\n떠 있는 모드에서는 왼쪽 손잡이로 위치를 옮길 수 있습니다.'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        group = QGroupBox(tr('바 모양'))
        form = QFormLayout(group)
        self.theme = self.combo(form, tr('테마'), [
            (tr('컬러 · 반투명'), "translucent"), (tr('컬러 · 배경'), "background"),
            (tr('단색'), "mono"),
        ], options.theme)
        self.size = self.combo(form, tr('크기'), [
            (tr('작게'), "compact"), (tr('기본'), "standard"), (tr('크게'), "prominent"),
        ], options.size)
        self.appearance = self.combo(form, tr('화면 모드'), [
            (tr('시스템'), "system"), (tr('밝게'), "light"), (tr('어둡게'), "dark"),
        ], options.appearance)
        self.length = self.combo(form, tr('바 길이'), [
            (tr('내용에 맞춤'), "auto"), (tr('넓게 · 최소 800px'), "wide"), (tr('화면 너비'), "full"),
        ], options.length)
        self.bar_placement = options.placement
        self.placement = self.combo(form, tr('배치'), [
            (tr('떠 있는 바 · 드래그로 이동'), "floating"),
            (tr('화면 상단 고정 · 작업 영역 확보'), "top"),
            (tr('화면 하단 고정 · 작업 영역 확보'), "bottom"),
            (tr('트레이 전용 · 바 숨김'), "tray"),
        ], "tray" if options.tray_mode else options.placement)
        def placement_changed():
            selected = self.placement.currentData()
            if selected != "tray":
                self.bar_placement = selected
            self.length.setEnabled(selected == "floating")
        self.placement.currentIndexChanged.connect(placement_changed)
        placement_changed()
        self.quota_display = self.combo(form, tr('비율 기준'), [
            (tr('사용량 · 사용한 비율'), "used"), (tr('잔량 · 남은 비율'), "remaining"),
        ], options.quota_display)
        self.reset_display = self.combo(form, tr('초기화까지'), [
            (tr('호버에서만 보기'), "off"), (tr('게이지 아래 · 한도 옆에 남은 시간'), "inline"),
        ], options.reset_display)
        self.icons = QCheckBox(tr('링 아이콘 표시'))
        self.icons.setChecked(options.icons)
        self.percentages = QCheckBox(tr('퍼센트 표시'))
        self.percentages.setChecked(options.percentages)
        form.addRow(self.icons)
        form.addRow(self.percentages)
        self.cpu_ai = QCheckBox(tr("로컬 AI 앱·CLI CPU 표시"))
        self.cpu_ai.setChecked(options.cpu_ai)
        self.cpu_programs = QCheckBox(tr("프로그램 CPU 표시"))
        self.cpu_programs.setChecked(options.cpu_programs)
        self.cpu_programs.setToolTip(tr("UsageDesk에서 실행하여 추적 가능한 프로세스만 표시합니다. 파일별 CPU는 제공되지 않을 수 있습니다."))
        form.addRow(self.cpu_ai)
        form.addRow(self.cpu_programs)
        layout.addWidget(group)
        limits = QGroupBox(tr('표시할 사용 한도'))
        limit_layout = QVBoxLayout(limits)
        self.mode = NoWheelComboBox()
        self.mode.addItem(tr('자동 · 데이터가 있는 모든 한도'), "smart")
        self.mode.addItem(tr('직접 선택'), "custom")
        self.mode.setCurrentIndex(self.mode.findData(options.mode))
        configure_popup(self.mode)
        limit_layout.addWidget(self.mode)
        self.hide_unselected = QCheckBox(tr('선택한 한도가 없는 서비스는 이름·로고도 숨기기'))
        self.hide_unselected.setChecked(options.hide_unselected)
        self.hide_unselected.setToolTip(tr('직접 선택 모드에서 적용됩니다. 모두 해제하면 서비스 전체가 바에서 사라집니다.'))
        limit_layout.addWidget(self.hide_unselected)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        choices = QVBoxLayout(content)
        self.checks = {}
        known = set()
        for provider, snapshot in snapshots.items():
            for quota in snapshot.limits:
                key = f"{provider}/{quota.key}"
                known.add(key)
                known.update(f"{provider}/{old}" for old, new in snapshot.aliases.items()
                             if new == quota.key)
                check = QCheckBox(f"{provider.title()} · {quota.label}")
                check.setChecked(quota_selected(snapshot, provider, quota.key, options.selected))
                self.checks[key] = check
                choices.addWidget(check)
        # Keep choices for temporarily disconnected accounts.
        self.unavailable = tuple(key for key in options.selected if key not in known)
        if not self.checks:
            choices.addWidget(QLabel(tr('계정 연결 후 조회된 한도를 선택할 수 있습니다.')))
        choices.addStretch()
        scroll.setWidget(content)
        limit_layout.addWidget(scroll)
        self.mode.currentIndexChanged.connect(
            lambda: content.setEnabled(self.mode.currentData() == "custom")
        )
        self.mode.currentIndexChanged.connect(
            lambda: self.hide_unselected.setEnabled(self.mode.currentData() == "custom")
        )
        self.hide_unselected.setEnabled(options.mode == "custom")
        content.setEnabled(options.mode == "custom")
        layout.addWidget(limits, 1)
        note = QLabel(tr('한도가 화면 너비를 넘으면 ‘+N’으로 묶습니다.\n클릭하면 상세 창에서 전체 한도를 확인할 수 있습니다.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        program_page = QWidget()
        program_page.setObjectName("programPage")
        self.tabs.addTab(program_page, tr('내 프로그램'))
        program_layout = QVBoxLayout(program_page)
        program_note = QLabel(tr('바 오른쪽에 표시할 프로그램을 선택하세요.\n체크는 표시만 바꾸며 프로그램을 실행하지 않습니다.'))
        program_note.setWordWrap(True)
        program_layout.addWidget(program_note)
        program_scroll = QScrollArea()
        program_scroll.setWidgetResizable(True)
        program_content = QWidget()
        program_choices = QVBoxLayout(program_content)
        self.program_checks = {}
        for entry in programs:
            check = QCheckBox(entry.name.replace("&", "&&"))
            check.setToolTip(entry.script_path)
            check.setChecked(entry.id in options.programs)
            self.program_checks[entry.id] = check
            program_choices.addWidget(check)
        self.unavailable_programs = tuple(k for k in options.programs if k not in self.program_checks)
        if not programs:
            empty = QLabel(tr('등록된 프로그램이 없습니다.\n상세 창의 ‘내 프로그램 → 추가’에서 CMD를 먼저 등록하세요.'))
            empty.setWordWrap(True)
            program_choices.addWidget(empty)
        program_choices.addStretch()
        program_scroll.setWidget(program_content)
        program_layout.addWidget(program_scroll, 1)
        program_layout.addWidget(QLabel(tr('모두 해제하면 바의 프로그램 영역을 숨깁니다.')))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr('저장'))
        buttons.button(QDialogButtonBox.Save).setObjectName("saveDisplay")
        buttons.button(QDialogButtonBox.Cancel).setText(tr('취소'))
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def refresh_theme(self, *_):
        palette = QPalette(self._light_palette)
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            for role in (QPalette.Window, QPalette.WindowText, QPalette.Base,
                         QPalette.AlternateBase, QPalette.Text, QPalette.Button,
                         QPalette.ButtonText, QPalette.Highlight, QPalette.HighlightedText,
                         QPalette.PlaceholderText):
                palette.setColor(group, role, QColor(theme_color(self._light_palette.color(group, role).name())))
        self.setPalette(palette)
        self.setStyleSheet(themed_style(self._light_style))
        for combo in self.findChildren(QComboBox):
            configure_popup(combo)

    def combo(self, layout, title, items, selected):
        combo = NoWheelComboBox()
        for label, value in items:
            combo.addItem(label, value)
        combo.setCurrentIndex(combo.findData(selected))
        configure_popup(combo)
        layout.addRow(title, combo)
        return combo

    def validate(self):
        if not self.icons.isChecked() and not self.percentages.isChecked():
            QMessageBox.warning(self, tr('표시 항목'), tr('링 아이콘 또는 퍼센트 중 하나를 선택하세요.'))
            return
        self.accept()

    def value(self):
        return DisplayOptions(
            size=self.size.currentData(), theme=self.theme.currentData(),
            appearance=self.appearance.currentData(), icons=self.icons.isChecked(),
            percentages=self.percentages.isChecked(), mode=self.mode.currentData(),
            selected=tuple(k for k, c in self.checks.items() if c.isChecked()) + self.unavailable,
            programs=tuple(k for k, c in self.program_checks.items() if c.isChecked())
            + self.unavailable_programs,
            length=self.length.currentData(), placement=self.bar_placement,
            tray_mode=self.placement.currentData() == "tray",
            hide_unselected=self.hide_unselected.isChecked(),
            cpu_ai=self.cpu_ai.isChecked(), cpu_programs=self.cpu_programs.isChecked(),
            quota_display=self.quota_display.currentData(),
            reset_display=self.reset_display.currentData(),
        )


class DisplayEditor(DisplayDialog):
    """The same tested display controls embedded in the single management window."""
    def __init__(self, owner):
        super().__init__(owner, owner.bar.options, owner.usage.snapshots, owner.entries)
        self.owner = owner
        self.setWindowFlags(Qt.Widget)
        self.tabs.setTabText(1, tr("바에 표시할 프로그램"))

    def accept(self):
        if self.owner.bar.apply_options(self.value(), self):
            self.owner.statusBar().showMessage(tr("표시 설정을 저장했습니다."))

    def reject(self):
        self.owner.reset_display_editor()


class UsageBar(QWidget):
    def __init__(self, owner, pane, directory):
        super().__init__(owner, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.owner = owner
        self.pane = pane
        self.store = DisplayStore(directory)
        self.options = self.store.load()
        self.setWindowTitle(tr('UsageDesk · 사용량 바'))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.items = []
        self.drag_offset = None
        self._positioned = False
        self._signature = None
        self.dock = TopDock(self)
        QApplication.instance().aboutToQuit.connect(self.dock.remove)
        self.service_images = {
            key: service_icon(key)
            for key in ("claude", "codex", "grok")
        }
        self.pane.changed.connect(self.refresh)
        app = QApplication.instance()
        app.screenAdded.connect(self.screen_added)
        app.screenRemoved.connect(self.relayout)
        app.paletteChanged.connect(self.refresh)
        for screen in app.screens():
            self.screen_added(screen)
        self.refresh()

    def screen_added(self, screen):
        screen.availableGeometryChanged.connect(self.relayout)
        self.relayout()

    def relayout(self, *_):
        if self.dock.busy:
            return
        self._signature = None
        self.refresh()

    def colors(self):
        dark = self.options.appearance == "dark" or (
            self.options.appearance == "system"
            and QApplication.palette().window().color().lightness() < 128
        )
        foreground = QColor("#f3f5f8" if dark else "#202633")
        background = QColor("#20242c" if dark else "#fafbfe")
        if self.options.theme == "translucent":
            background.setAlpha(232)
        return foreground, background, QColor("#444c5b" if dark else "#dce1e9")

    def refresh(self, *_):
        # Keep pointer targets stable on the one-second controller tick.
        stale = tuple(
            (key, bool(self.pane.errors.get(key)) or
             (datetime.now(UTC) - snapshot.fetched_at).total_seconds() > 360)
            for key, snapshot in self.pane.snapshots.items()
        )
        signature = (self.options, repr(self.pane.snapshots), tuple(self.pane.errors.items()),
                     int(datetime.now(UTC).timestamp()) // 60
                     if self.options.reset_display != "off" else None,
                     tuple(self.pane.tasks), stale,
                     tuple(card[0].text() for card in self.pane.cards.values()),
                     tuple(getattr(self.owner, "entries", ())))
        if signature != self._signature:
            self._signature = signature
            self.rebuild(dict(stale))
        self.update()

    def rebuild(self, stale):
        height, font_size = {"compact": (34, 9), "standard": (42, 10), "prominent": (50, 12)}[
            self.options.size
        ]
        self.setFont(QFont("Segoe UI", font_size))
        metrics = QFontMetrics(self.font())
        caption_font = QFont(self.font())
        caption_font.setPointSizeF(max(7, font_size - 2))
        caption_metrics = QFontMetrics(caption_font if self.options.percentages else self.font())
        available = self.available_geometry()
        maximum = max(200, available.width() - (0 if self.options.placement in ("top", "bottom") else 24))
        self.program_entries = [e for e in getattr(self.owner, "entries", ())
                                if e.id in self.options.programs]
        providers = [(key, name) for key, name in
                     (("claude", "Claude"), ("codex", "Codex"), ("grok", "Grok"))
                     if key in self.pane.cards]
        if self.options.hide_unselected and self.options.mode == "custom":
            providers = [(key, name) for key, name in providers
                         if any(s.startswith(key + "/") for s in self.options.selected)]
        budgets, demands = {}, {}
        for provider, name in providers:
            snapshot = self.pane.snapshots.get(provider)
            title_width = max(metrics.horizontalAdvance(name),
                              caption_metrics.horizontalAdvance("CPU 100.0%") if self.options.cpu_ai else 0) + 42
            quotas = visible_limits(snapshot, provider, self.options)
            if quotas:
                content = sum(max(metrics.horizontalAdvance(
                    f"{display_percent(q, self.options, provider=provider, snapshot=snapshot):g}%" if self.options.percentages else ""),
                    caption_metrics.horizontalAdvance(quota_caption(q, self.options, provider=provider, snapshot=snapshot)))
                    + (25 if self.options.icons else 0) + 16 for q in quotas)
            else:
                text = tr('조회 중…') if provider in self.pane.tasks else (
                    tr('로그인') if snapshot is None else tr('선택 없음'))
                if snapshot and snapshot.notes:
                    text = snapshot.notes[0][:24]
                content = metrics.horizontalAdvance(text) + 18
            demands[provider] = title_width + max(44, content)
            budgets[provider] = title_width + 44
        # Two-line full names, then tighter spacing, one font step, and spare bar space.
        normal_budget = min(360, maximum // 3)
        self.cpu_program_width = caption_metrics.horizontalAdvance("CPU 100.0%") + 12 if self.options.cpu_programs else 0
        self.program_padding = 6
        self.program_font = QFont(self.font())
        self.program_font.setPointSize(max(8, font_size - 1))

        def program_rows():
            program_metrics = QFontMetrics(self.program_font)
            rows = []
            for entry in self.program_entries:
                text = program_label(entry.name, program_metrics)
                width = max(program_metrics.horizontalAdvance(line) for line in text.split("\n"))
                rows.append((width + self.program_padding * 2 + self.cpu_program_width, "program", text, entry.id,
                             tr('{p0}\n{p1}\n클릭하여 열기', p0=entry.name, p1=entry.script_path)
                             + (cpu_tooltip(self.owner, entry.id) if self.options.cpu_programs else "")))
            return rows

        rows = program_rows()
        if sum(row[0] for row in rows) > normal_budget:
            self.program_padding = 4
            rows = program_rows()
        if sum(row[0] for row in rows) > normal_budget:
            self.program_font.setPointSize(max(8, font_size - 2))
            rows = program_rows()
        # Height must accommodate both lines at the current DPI, without shrinking gauges.
        if rows:
            height = max(height, QFontMetrics(self.program_font).lineSpacing() * 2 + 4)
        available_content = max(0, maximum - 28 - 96 - 8 * len(providers))
        spare = max(0, available_content - sum(demands.values()) - 12)
        program_budget = min(max(normal_budget, spare),
                             max(44, available_content - sum(budgets.values()) - 12))
        program_items, self.hidden_programs = [], []
        program_width = 0
        for index, row in enumerate(rows):
            reserve = 44 if index < len(rows) - 1 else 0
            if program_width + row[0] + reserve > program_budget:
                self.hidden_programs = self.program_entries[index:]
                program_items.append((44, "programs", f"+{len(self.hidden_programs)}", "",
                                      tr('바에 선택한 나머지 프로그램 · 전체 이름으로 표시')))
                program_width += 44
                break
            program_items.append(row)
            program_width += row[0]
        if program_items:
            program_width += 12
        total_budget = max(0, available_content - program_width)
        if sum(budgets.values()) > total_budget:
            budgets = dict.fromkeys(budgets, total_budget // len(providers))
        else:
            remaining_budget = total_budget - sum(budgets.values())
            while remaining_budget:
                needy = [key for key in budgets if budgets[key] < demands[key]]
                if not needy:
                    break
                share = max(1, remaining_budget // len(needy))
                for key in needy:
                    added = min(share, demands[key] - budgets[key], remaining_budget)
                    budgets[key] += added
                    remaining_budget -= added
        items = []
        x = 28
        for provider, name in providers:
            budget = budgets[provider]
            snapshot = self.pane.snapshots.get(provider)
            title = name
            width = min(max(metrics.horizontalAdvance(title),
                            caption_metrics.horizontalAdvance("CPU 100.0%") if self.options.cpu_ai else 0) + 42, max(35, budget - 44))
            status = self.pane.cards[provider][0].text()
            if self.options.cpu_ai:
                status += cpu_tooltip(self.owner, provider)
            items.append((QRect(x, 0, width, height), "provider", title, None, provider, status))
            x += width
            remaining = budget - width
            quotas = visible_limits(snapshot, provider, self.options)
            if not quotas:
                text = tr('조회 중…') if provider in self.pane.tasks else (
                    tr('로그인') if snapshot is None else tr('선택 없음')
                )
                if snapshot and snapshot.notes:
                    text = snapshot.notes[0][:24]
                width = min(max(40, remaining), metrics.horizontalAdvance(text) + 18)
                items.append((QRect(x, 0, width, height), "detail", text, None, provider, status))
                x += width
            for index, quota in enumerate(quotas):
                text = f"{display_percent(quota, self.options, provider=provider, snapshot=snapshot):g}%" if self.options.percentages else ""
                caption = quota_caption(quota, self.options, provider=provider, snapshot=snapshot)
                width = max(metrics.horizontalAdvance(text), caption_metrics.horizontalAdvance(caption))
                width += (25 if self.options.icons else 0) + 16
                reserve = 44 if index < len(quotas) - 1 else 0
                if width + reserve > remaining:
                    count = len(quotas) - index
                    items.append((QRect(x, 0, 44, height), "detail", f"+{count}", None,
                                  provider, tr('상세 창에서 전체 한도 보기')))
                    x += 44
                    break
                tooltip = tr('{p0} · {p1}\n사용 {p2:g}%', p0=name, p1=quota.label, p2=quota.used_percent)
                if quota.reset_at_utc:
                    tooltip += tr('\n초기화 {p0:%m-%d %H:%M}', p0=quota.reset_at_utc.astimezone())
                tooltip += weekly_explanation(quota, provider, snapshot)
                tooltip += f"\n{status}"
                items.append((QRect(x, 0, width, height), "detail", text, quota, provider, tooltip))
                x += width
                remaining -= width
            x += 8
        content_width = x + program_width + 96
        target_width = content_width
        if self.options.length == "wide":
            target_width = max(content_width, min(800, maximum))
        if self.options.length == "full" or self.options.placement in ("top", "bottom"):
            target_width = maximum
        x = max(x, target_width - program_width - 96)
        self.program_separator = x if program_items else None
        if program_items:
            x += 12
        for width, action, text, entry_id, tooltip in program_items:
            items.append((QRect(x, 0, width, height), action, text, None, entry_id, tooltip))
            x += width
        for action, text, tooltip in (
            ("refresh", "↻", tr('사용량 새로고침')),
            ("settings", "⚙", tr('표시 설정')),
            ("menu", "⋯", tr('프로그램 · 위치 초기화 · 종료')),
        ):
            items.append((QRect(x, 0, 30, height), action, text, None, "", tooltip))
            x += 30
        self.items = items
        self.setFixedSize(x + 6, height)
        self.setAccessibleName(tr('UsageDesk 사용량 바. ') + "; ".join(item[5] for item in items))
        self.reposition()

    def available_geometry(self):
        app = QApplication.instance()
        screen = app.screenAt(self.frameGeometry().center()) if self._positioned else None
        target = screen or app.primaryScreen()
        return target.geometry() if self.options.placement in ("top", "bottom") else target.availableGeometry()

    def reposition(self, *_):
        if self.options.tray_mode:
            return
        if self.options.placement in ("top", "bottom"):
            self.dock.schedule()
            return
        if self._positioned:
            self.move(keep_on_screen(self.geometry(), self.available_geometry()))

    def tray_only(self):
        if not getattr(self.owner, "tray_available", True):
            QMessageBox.warning(self, tr('트레이 사용 불가'), tr('시스템 트레이를 사용할 수 없어 바를 유지합니다.'))
            return
        options = replace(self.options, tray_mode=True)
        try:
            if self.isVisible():
                self.store.save_position(self.pos())
            self.store.save(options)
        except OSError:
            QMessageBox.warning(self, tr('저장 실패'), tr('트레이 모드를 저장할 수 없습니다.'))
            return
        self.options = options
        self.dock.remove()
        self.hide()
        self.owner.hide()

    def restore_bar(self):
        if self.options.tray_mode:
            options = replace(self.options, tray_mode=False)
            try:
                self.store.save(options)
            except OSError:
                QMessageBox.warning(self, tr('저장 실패'), tr('바 표시 설정을 저장할 수 없습니다.'))
                return
            self.options = options
            self.refresh()
        self.show_bar()

    def show_bar(self):
        if self.options.tray_mode and getattr(self.owner, "tray_available", True):
            self.dock.remove()
            self.hide()
            return
        if not self._positioned:
            position = self.store.position()
            screen = QApplication.screenAt(position) if position is not None else None
            area = (screen or QApplication.primaryScreen()).availableGeometry()
            if position is None:
                position = QPoint(area.right() - self.width() - 12, area.top() + 10)
            self.move(keep_on_screen(QRect(position, self.size()), area))
            self._positioned = True
            self.relayout()
        self.reposition()
        self.show()
        self.raise_()
        if self.options.placement in ("top", "bottom"):
            self.dock.update()

    def reset_position(self):
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 12, area.top() + 10)
        self.reposition()
        self.persist_position()
        self.show_bar()

    def persist_position(self):
        try:
            self.store.save_position(self.pos())
        except OSError:
            self.setToolTip(tr('위치 저장 실패 · 이번 실행에서만 적용됩니다.'))

    def settings_dialog(self):
        if hasattr(self.owner, "open_settings"):
            self.owner.open_settings(4)
            return
        dialog = DisplayDialog(self, self.options, self.pane.snapshots,
                               getattr(self.owner, "entries", ()))
        if dialog.exec() == QDialog.Accepted:
            self.apply_options(dialog.value(), dialog)
        dialog.deleteLater()

    def apply_options(self, options, dialog=None):
        if options.tray_mode and not getattr(self.owner, "tray_available", True):
            QMessageBox.warning(dialog, tr('트레이 사용 불가'), tr('시스템 트레이를 사용할 수 없어 기존 배치를 유지합니다.'))
            return False
        try:
            if options.tray_mode and self.isVisible():
                self.store.save_position(self.pos())
            self.store.save(options)
        except OSError:
            QMessageBox.warning(dialog, tr('저장 실패'), tr('표시 설정을 저장할 수 없습니다.'))
            return False
        self.dock.remove()
        self.options = options
        self.refresh()
        if self.options.tray_mode:
            self.tray_only()
            return True
        self.show_bar()
        if self.options.placement in ("top", "bottom"):
            self.dock.update()
        else:
            position = self.store.position()
            if position is not None:
                self.move(position)
            self.reposition()
        return True

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        foreground, background, border = self.colors()
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 11, 11)
        painter.setPen(border)
        for column in (11, 16):
            for row in (-5, 0, 5):
                painter.drawPoint(column, self.height() // 2 + row)
        if self.program_separator is not None:
            painter.drawLine(self.program_separator + 5, 10, self.program_separator + 5,
                             self.height() - 10)
        for rect, action, text, quota, provider, _tooltip in self.items:
            painter.setPen(foreground)
            if action == "provider":
                pixmap = self.service_images[provider]
                painter.drawPixmap(QRect(rect.left() + 5, (rect.height() - 23) // 2, 23, 23), pixmap)
                caption = painter.fontMetrics().elidedText(text, Qt.ElideRight, rect.width() - 28)
                painter.drawText(rect.adjusted(28, 0, 0, -12 if self.options.cpu_ai else 0), Qt.AlignCenter, caption)
                if self.options.cpu_ai:
                    painter.save()
                    font = QFont(self.font())
                    font.setPointSizeF(max(7, font.pointSizeF() - 2))
                    painter.setFont(font)
                    painter.drawText(rect.adjusted(28, 17, 0, 0), Qt.AlignCenter, cpu_text(self.owner, provider))
                    painter.restore()
                continue
            if action in ("program", "programs"):
                painter.save()
                painter.setFont(self.program_font)
                padding = self.program_padding if action == "program" else 4
                cpu_width = self.cpu_program_width if action == "program" else 0
                painter.drawText(rect.adjusted(padding, 0, -padding-cpu_width, 0), Qt.AlignCenter, text)
                if cpu_width:
                    font = QFont(self.font())
                    font.setPointSizeF(max(7, font.pointSizeF() - 2))
                    painter.setFont(font)
                    painter.drawText(QRect(rect.right()-cpu_width, 0, cpu_width, rect.height()),
                                     Qt.AlignCenter, cpu_text(self.owner, provider))
                painter.restore()
                continue
            if action != "detail":
                # Draw controls as vectors: no missing glyphs on Windows/offscreen fonts.
                center = rect.center()
                x, y = center.x(), center.y()
                painter.setPen(QPen(foreground, 1.5, Qt.SolidLine, Qt.RoundCap))
                painter.setBrush(Qt.NoBrush)
                if action == "refresh":
                    painter.drawArc(QRectF(x - 6, y - 6, 12, 12), 45 * 16, 290 * 16)
                    painter.drawLine(x + 5, y - 6, x + 5, y - 1)
                    painter.drawLine(x + 5, y - 1, x, y - 1)
                elif action == "settings":
                    for offset, knob in ((-5, -2), (0, 3), (5, -3)):
                        painter.drawLine(x - 7, y + offset, x + 7, y + offset)
                        painter.setBrush(background)
                        painter.drawEllipse(QRectF(x + knob - 2, y + offset - 2, 4, 4))
                else:
                    painter.setBrush(foreground)
                    for offset in (-5, 0, 5):
                        painter.drawEllipse(QRectF(x + offset - 1, y - 1, 2, 2))
                continue
            if quota is None:
                painter.drawText(rect, Qt.AlignCenter, text)
                continue
            snapshot = self.pane.snapshots.get(provider)
            color = QColor("#20bba5" if provider == "codex" else "#4eaf72")
            if quota.window_seconds and quota.window_seconds >= 86400 or "seven_day" in quota.key:
                color = QColor("#5297f2" if provider == "codex" else "#9b7aeb")
            if exhausted_weekly(quota, provider, snapshot) is not None or quota.used_percent >= 90:
                color = QColor("#e56b72")
            elif quota.used_percent >= 75:
                color = QColor("#dba344")
            if self.options.theme == "mono":
                color = foreground
            left = rect.left() + 5
            if self.options.icons:
                circle = QRectF(left, (rect.height() - 19) / 2, 19, 19)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(border, 3))
                painter.drawEllipse(circle)
                painter.setPen(QPen(color, 3, Qt.SolidLine, Qt.RoundCap))
                painter.drawArc(circle, 90 * 16,
                                -round(min(100, display_percent(quota, self.options, provider=provider, snapshot=snapshot)) * 3.6 * 16))
                left += 25
            painter.setPen(foreground)
            text_rect = QRect(left, rect.top(), rect.right() - left, rect.height())
            if self.options.percentages:
                painter.drawText(text_rect.adjusted(0, 0, 0, -12), Qt.AlignVCenter | Qt.AlignLeft, text)
                font = painter.font()
                small = QFont(font)
                small.setPointSizeF(max(7, font.pointSizeF() - 2))
                painter.setFont(small)
                painter.setPen(color)
                painter.drawText(text_rect.adjusted(0, 17, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                                 quota_caption(quota, self.options, provider=provider, snapshot=snapshot))
                painter.setFont(font)
            else:
                painter.drawText(text_rect, Qt.AlignCenter, quota_caption(quota, self.options, provider=provider, snapshot=snapshot))

    def tooltip_at(self, position, now=None):
        for rect, _action, _text, quota, provider, tooltip in self.items:
            if not rect.contains(position):
                continue
            if quota is None:
                return tooltip, rect
            now = now or datetime.now(UTC)
            text = (tr('{p0} · {p1}\n사용 {p2:g}% · 남음 {p3:g}%', p0=provider.title(), p1=quota.label, p2=quota.used_percent, p3=quota.remaining_percent))
            if quota.reset_at_utc:
                seconds = max(0, int((quota.reset_at_utc - now).total_seconds()))
                remaining = (tr('{p0}일 {p1}시간 {p2}분 남음', p0=seconds // 86400, p1=seconds % 86400 // 3600, p2=seconds % 3600 // 60) if seconds >= 60
                             else tr('{p0}초 남음', p0=seconds) if seconds else tr('초기화 확인 대기'))
                text += (tr('\n초기화 {p0:%Y-%m-%d %H:%M %Z}\n{p1}', p0=quota.reset_at_utc.astimezone(), p1=remaining))
            else:
                text += tr('\n초기화 시간 미제공')
            text += weekly_explanation(quota, provider, self.pane.snapshots.get(provider))
            text += "\n" + self.pane.cards[provider][0].text()
            return text, rect
        return (tr('고정된 바 · 표시 설정에서 배치 변경') if self.options.placement in ("top", "bottom")
                else tr('드래그하여 이동 · 오른쪽 클릭으로 메뉴 열기')), QRect(0, 0, 28, self.height())

    def event(self, event):
        if event.type() == QEvent.ToolTip and hasattr(self, "items"):
            if self.drag_offset is None:
                text, rect = self.tooltip_at(event.pos())
                QToolTip.showText(event.globalPos(), text, self, rect, 20000)
                event.accept()
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        if event.type() in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress):
            QToolTip.hideText()
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.owner.tray_menu.popup(event.globalPosition().toPoint())
        elif (event.button() == Qt.LeftButton and event.position().x() < 28
              and self.options.placement == "floating"):
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None:
            position = event.globalPosition().toPoint() - self.drag_offset
            screen = QApplication.screenAt(event.globalPosition().toPoint())
            if screen:
                position = keep_on_screen(QRect(position, self.size()), screen.availableGeometry())
            self.move(position)
            return
        self.setCursor(Qt.OpenHandCursor if event.position().x() < 28
                       and self.options.placement == "floating" else Qt.PointingHandCursor)
        for rect, _action, _text, _quota, _provider, tooltip in self.items:
            if rect.contains(event.position().toPoint()):
                self.setToolTip(tooltip)
                return
        self.setToolTip(tr('고정된 바 · 표시 설정에서 배치 변경') if self.options.placement in ("top", "bottom")
                        else tr('드래그하여 이동 · 오른쪽 클릭으로 메뉴 열기'))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.drag_offset is not None:
            self.drag_offset = None
            self._positioned = True
            self.relayout()
            self.persist_position()
            self.setCursor(Qt.OpenHandCursor)
            return
        for rect, action, _text, _quota, provider, _tooltip in self.items:
            if rect.contains(event.position().toPoint()):
                if action in ("detail", "provider"):
                    self.owner.open_tab(0)
                elif action == "program":
                    entry = next((e for e in getattr(self.owner, "entries", ())
                                  if e.id == provider), None)
                    if entry is not None:
                        self.owner.launch(entry)
                elif action == "programs":
                    previous_menu = getattr(self, "program_menu", None)
                    if previous_menu is not None:
                        previous_menu.deleteLater()
                    self.program_menu = QMenu(self)
                    for entry in self.hidden_programs:
                        self.program_menu.addAction(entry.name.replace("&", "&&"),
                                                    lambda e=entry: self.owner.launch(e))
                    self.program_menu.popup(event.globalPosition().toPoint())
                elif action == "refresh":
                    self.pane.refresh_all()
                elif action == "settings":
                    self.settings_dialog()
                elif action == "menu":
                    self.owner.tray_menu.popup(event.globalPosition().toPoint())
                break

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.owner.open_tab(0)
        elif event.key() == Qt.Key_Menu:
            self.owner.tray_menu.popup(self.mapToGlobal(QPoint(0, self.height())))
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if not self.owner.quitting:
            event.ignore()
            self.owner.open_tab(0)
        else:
            self.dock.remove()
            event.accept()

    def nativeEvent(self, event_type, message):
        if hasattr(self, "dock"):
            self.dock.native_event(message)
        return super().nativeEvent(event_type, message)

    def hideEvent(self, event):
        if hasattr(self, "dock"):
            self.dock.remove()
        super().hideEvent(event)
