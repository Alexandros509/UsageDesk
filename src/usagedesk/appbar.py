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


class TopDock:
    def __init__(self, widget):
        self.widget = widget
        self.registered = False
        self.busy = False
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
        self.user.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user.GetDpiForWindow.restype = wintypes.UINT
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
            scale = (self.user.GetDpiForWindow(data.hWnd) or 96) / 96
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
            self.send(3, data)  # ABM_SETPOS
            screen = self.widget.screen().geometry()
            rect = QRect(screen.left() + round((data.rc.left - info.rcMonitor.left) / scale),
                         screen.top() + round((data.rc.top - info.rcMonitor.top) / scale),
                         round((data.rc.right - data.rc.left) / scale),
                         round((data.rc.bottom - data.rc.top) / scale))
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
            self.busy = False

    def schedule(self):
        if not self.busy and not self.timer.isActive():
            self.timer.start(60)

    def remove(self):
        self.timer.stop()
        self.bounds = None
        if self.registered:
            self.registered = False
            self.send(1)  # ABM_REMOVE

    def native_event(self, message):
        if not self.callback:
            return
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message == self.taskbar_created:
            self.registered = False
            self.schedule()
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
