"""Windows Shell AppBar registration; no persistent system-setting changes."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QRect, QTimer
from PySide6.QtWidgets import QApplication

from .i18n import tr


class AppBarData(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def matching_screen(monitor, screens):
    """Qt screen showing the Windows monitor rectangle (left, top, right, bottom), or None.

    A Qt screen keeps the native origin and divides only its size by the scale factor. While
    the display layout is changing, Qt and Windows can briefly describe different monitors;
    None then means "not settled yet" rather than a guess in mixed units.
    """
    left, top, right, bottom = monitor
    for screen in screens:
        area, scale = screen.geometry(), screen.devicePixelRatio()
        if (area.left() == left and area.top() == top
                and abs(area.width() - (right - left) / scale) <= 1
                and abs(area.height() - (bottom - top) / scale) <= 1):
            return screen
    return None


def logical_rect(native, monitor, area, scale):
    """Native AppBar rectangle on `monitor` in the logical pixels of its Qt screen `area`."""
    return QRect(area.left() + round((native[0] - monitor[0]) / scale),
                 area.top() + round((native[1] - monitor[1]) / scale),
                 round((native[2] - native[0]) / scale),
                 round((native[3] - native[1]) / scale))


class TopDock:
    RETRIES = 20  # 250 ms apart: display changes settle within a few seconds.

    def __init__(self, widget):
        self.widget = widget
        self.registered = False
        self.busy = False
        self.pending = False
        self.retries = 0
        self.applied = None
        self.callback = 0
        self.taskbar_created = 0
        self.bounds = None
        self.shell = self.user = None
        self.timer = QTimer(widget)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update)

    def setup(self):
        if self.shell is not None:
            return
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(AppBarData)]
        self.shell.SHAppBarMessage.restype = ctypes.c_size_t
        self.user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self.user.RegisterWindowMessageW.restype = wintypes.UINT
        self.user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user.MonitorFromWindow.restype = wintypes.HANDLE
        self.user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        self.user.GetMonitorInfoW.restype = wintypes.BOOL
        self.user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.SetWindowPos.restype = wintypes.BOOL
        self.callback = self.user.RegisterWindowMessageW("UsageDesk.TopDock.Callback")
        self.taskbar_created = self.user.RegisterWindowMessageW("TaskbarCreated")

    def data(self):
        data = AppBarData()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = int(self.widget.winId())
        data.uCallbackMessage = self.callback
        data.uEdge = 3 if self.widget.options.placement == "bottom" else 1  # ABE_BOTTOM/TOP
        return data

    def send(self, message, data=None):
        data = data if data is not None else self.data()
        return self.shell.SHAppBarMessage(message, ctypes.byref(data))

    def update(self):
        if (self.busy or not self.widget.isVisible() or self.widget.options.tray_mode
                or self.widget.options.placement not in ("top", "bottom")):
            return
        # Offscreen rendering never changes the desktop work area.
        if QApplication.platformName() != "windows":
            area = self.widget.screen().geometry()
            self.widget.move(area.left(), area.bottom() - self.widget.height() + 1
                             if self.widget.options.placement == "bottom" else area.top())
            return
        self.busy = True
        try:
            self.setup()
            if not self.registered:
                self.registered = bool(self.send(0))  # ABM_NEW
                if not self.registered:
                    raise OSError("AppBar registration failed")
            data = self.data()
            monitor = self.user.MonitorFromWindow(data.hWnd, 2)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                raise OSError("Monitor geometry unavailable")
            monitor = (info.rcMonitor.left, info.rcMonitor.top,
                       info.rcMonitor.right, info.rcMonitor.bottom)
            # Size and scale come from the one Qt screen that shows this monitor. The window's
            # own DPI lags behind while it is being moved between monitors.
            screen = matching_screen(monitor, QApplication.screens())
            if screen is None:
                if self.retries < self.RETRIES:
                    self.retries += 1
                    self.timer.start(250)
                return
            self.retries = 0
            scale = screen.devicePixelRatio()
            height = round(self.widget.height() * scale)
            data.rc = info.rcMonitor
            if data.uEdge == 3:
                data.rc.top = data.rc.bottom - height
            else:
                data.rc.bottom = data.rc.top + height
            self.send(2, data)  # ABM_QUERYPOS
            if data.uEdge == 3:
                data.rc.top = data.rc.bottom - height
            else:
                data.rc.bottom = data.rc.top + height
            native = (data.rc.left, data.rc.top, data.rc.right, data.rc.bottom)
            if self.applied != (data.uEdge, native):  # Repeated passes must not notify again.
                self.send(3, data)  # ABM_SETPOS
                native = (data.rc.left, data.rc.top, data.rc.right, data.rc.bottom)
                self.applied = (data.uEdge, native)
            rect = logical_rect(native, monitor, screen.geometry(), scale)
            changed = self.bounds != rect
            self.bounds = rect
            self.widget.setFixedSize(rect.size())
            self.widget.move(rect.topLeft())
            if changed:
                self.widget._signature = None
                QTimer.singleShot(0, self.widget.refresh)
        except (OSError, AttributeError):
            self.remove()
            self.widget.setToolTip(tr('고정 바 작업 영역 확보 실패 · 표시 설정에서 떠 있는 바로 전환하세요.'))
        finally:
            self.finish()

    def finish(self):
        self.busy = False
        if self.pending:  # A change arrived during this pass; its values may be stale.
            self.pending = False
            self.schedule()

    def schedule(self):
        if self.busy:
            self.pending = True
        elif not self.timer.isActive():
            self.timer.start(60)

    def remove(self):
        self.timer.stop()
        self.bounds = self.applied = None
        self.pending = False
        self.retries = 0
        if self.registered:
            self.registered = False
            self.send(1)  # ABM_REMOVE

    def native_event(self, message):
        if not self.callback:
            return
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message == self.taskbar_created:
            self.registered = False
            self.applied = None
            self.schedule()
        elif msg.message in (0x007E, 0x02E0):  # WM_DISPLAYCHANGE, WM_DPICHANGED
            # Lay out again once Qt has processed the same message.
            QTimer.singleShot(0, self.widget.relayout)
        elif self.registered and msg.message == self.callback:
            if msg.wParam == 1:  # ABN_POSCHANGED
                self.schedule()
            elif msg.wParam == 2:  # ABN_FULLSCREENAPP: yield to full-screen applications.
                self.user.SetWindowPos(msg.hWnd, 1 if msg.lParam else -1, 0, 0, 0, 0, 0x13)
        elif self.registered and not self.busy:
            if msg.message == 0x0006:  # WM_ACTIVATE
                self.send(6)
            elif msg.message == 0x0047:  # WM_WINDOWPOSCHANGED
                self.send(9)
