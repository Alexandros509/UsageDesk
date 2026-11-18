"""Monitor identity and selection; no display configuration or registry writes."""

import ctypes
import hashlib
import json
import sys
from ctypes import wintypes

from PySide6.QtGui import QGuiApplication

from .i18n import tr


def device_interface(name):
    """Read the Windows per-monitor interface instead of a transient list index."""
    if (sys.platform != 'win32' or QGuiApplication.instance() is None
            or QGuiApplication.platformName() != 'windows'):
        return ''

    class DisplayDevice(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('name', wintypes.WCHAR * 32),
                    ('description', wintypes.WCHAR * 128), ('flags', wintypes.DWORD),
                    ('identifier', wintypes.WCHAR * 128), ('key', wintypes.WCHAR * 128)]

    try:
        function = ctypes.WinDLL('user32', use_last_error=True).EnumDisplayDevicesW
        function.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                             ctypes.POINTER(DisplayDevice), wintypes.DWORD]
        function.restype = wintypes.BOOL
        for index in range(16):
            device = DisplayDevice()
            device.cb = ctypes.sizeof(device)
            if not function(name, index, ctypes.byref(device), 1):
                break
            if device.flags & 1 and device.identifier:
                return device.identifier.casefold()
    except (OSError, AttributeError):
        pass  # Qt identity remains available on virtual/unsupported display drivers.
    return ''


def screen_id(screen):
    """Persist only a hash, never the monitor's raw serial or device path."""
    serial = screen.serialNumber().strip()
    if serial and set(serial) != {'0'}:
        identity = ['serial', screen.manufacturer(), screen.model(), serial]
    else:
        interface = device_interface(screen.name())
        identity = ['device', interface] if interface else ['name', screen.name(),
                                                          screen.manufacturer(), screen.model()]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def screen_label(screen, primary=None):
    name = screen.name().removeprefix('\\\\.\\')
    model = screen.model().strip()
    title = name + (' · ' + model if model and model != name else '')
    area, scale = screen.geometry(), screen.devicePixelRatio()
    title += f' · {round(area.width() * scale)}×{round(area.height() * scale)}'
    return title + (tr(' · 주 모니터') if screen is primary else '')


def screen_at(point, screens):
    return next((s for s in screens if point is not None and s.geometry().contains(point)), None)


def choose_screen(screens, primary, mode, identifier, position=None, current=None):
    """Resolve a preference, preserving a missing identity for a later reconnect."""
    if not screens:
        return None
    primary = primary if primary in screens else screens[0]
    if mode == 'primary':
        return primary
    if identifier:
        return next((s for s in screens if screen_id(s) == identifier), primary)
    return screen_at(position, screens) or (current if current in screens else primary)
