import ctypes
from ctypes import wintypes

import pytest
from PySide6.QtWidgets import QApplication

from usagedesk.hotkeys import ACTIONS, HotkeyPage, Hotkeys, WindowsKeys, parse_key


class FakeKeys:
    def __init__(self):
        self.registered = {}
        self.conflicts = set()

    def register(self, identifier, combo):
        if combo in self.conflicts:
            return False
        assert identifier not in self.registered
        self.registered[identifier] = combo
        return True

    def unregister(self, identifier):
        self.registered.pop(identifier)


def config(**keys):
    return {"enabled": True, "keys": dict.fromkeys(ACTIONS, "") | keys}


@pytest.fixture
def manager(tmp_path):
    app = QApplication.instance() or QApplication([])
    calls = []
    backend = FakeKeys()
    manager = Hotkeys(tmp_path, {a: lambda a=a: calls.append(a) for a in ACTIONS}, backend=backend)
    yield manager, backend, calls
    manager.close()
    app.processEvents()


@pytest.mark.parametrize("text", ["A", "Shift+A", "Meta+U", "Ctrl+F12", "Ctrl+U, Ctrl+V"])
def test_rejects_unmodified_reserved_and_multi_step_keys(text):
    with pytest.raises(ValueError):
        parse_key(text)


def test_defaults_disabled_and_no_registration(manager):
    m, backend, _ = manager
    assert not m.config["enabled"] and not backend.registered
    assert not m.path.exists()


def test_register_dispatch_disable_and_restart(manager):
    m, backend, calls = manager
    m.apply(config(toggle="Ctrl+Alt+U", refresh="Ctrl+Shift+R"))
    for identifier, _ in m.bindings.values():
        m.dispatch(identifier)
    assert calls == ["toggle", "refresh"]
    m.close()
    restored = Hotkeys(m.path.parent, m.callbacks, backend=backend)
    assert len(restored.bindings) == 2
    restored.apply(restored.config | {"enabled": False})
    assert not backend.registered and not restored.config["enabled"]
    restored.close()


def test_conflict_and_save_failure_keep_previous_registration(manager, monkeypatch):
    m, backend, _ = manager
    m.apply(config(toggle="Ctrl+Alt+U"))
    old_file, old_bindings = m.path.read_bytes(), dict(m.bindings)
    backend.conflicts.add(parse_key("Ctrl+Alt+P"))
    with pytest.raises(ValueError):
        m.apply(config(toggle="Ctrl+Alt+I", usage="Ctrl+Alt+P"))
    assert m.bindings == old_bindings and len(backend.registered) == 1
    assert m.path.read_bytes() == old_file
    def fail(_):
        raise OSError("disk full")
    monkeypatch.setattr(m, "write", fail)
    with pytest.raises(OSError):
        m.apply(config(toggle="Ctrl+Alt+T"))
    assert m.bindings == old_bindings and len(backend.registered) == 1
    assert m.path.read_bytes() == old_file


def test_swap_actions_reuses_registered_combos_and_duplicate_rejected(manager):
    m, backend, calls = manager
    m.apply(config(toggle="Ctrl+Alt+U", usage="Ctrl+Alt+I"))
    before = dict(backend.registered)
    m.apply(config(toggle="Ctrl+Alt+I", usage="Ctrl+Alt+U"))
    assert backend.registered == before
    m.dispatch(m.bindings[parse_key("Ctrl+Alt+U")][0])
    assert calls == ["usage"]
    with pytest.raises(ValueError):
        m.apply(config(toggle="Ctrl+Alt+I", usage="Ctrl+Alt+I"))


def test_native_message_and_editor_suppression(manager):
    m, _, calls = manager
    m.apply(config(toggle="Ctrl+Alt+U"))
    msg = wintypes.MSG()
    msg.message = 0x312
    msg.wParam = next(iter(m.bindings.values()))[0]
    m.blocked = lambda: True
    assert m.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg)) == (True, 0)
    assert not calls
    m.blocked = lambda: False
    m.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg))
    assert calls == ["toggle"]
    m.close()
    m.nativeEventFilter(b"windows_dispatcher_MSG", ctypes.addressof(msg))
    assert calls == ["toggle"]


def test_page_capture_save_clear_and_disable(manager):
    m, backend, _ = manager
    page = HotkeyPage(m)
    page.enabled.setChecked(True)
    page.edits["toggle"].setKeySequence("Ctrl+Alt+U")
    page.save()
    assert m.config["keys"]["toggle"] == "Ctrl+Alt+U"
    page.reset()
    page.save()
    assert not backend.registered and not m.config["enabled"]
    page.deleteLater()


def test_windows_registration_conflict_no_repeat_and_release():
    app = QApplication.instance() or QApplication([])
    backend = WindowsKeys()
    combo = parse_key("Ctrl+Alt+Shift+F11")
    if not backend.register(0x5FFE, combo):
        pytest.skip("Native test combination already in use")
    try:
        assert not backend.register(0x5FFF, combo)
    finally:
        backend.unregister(0x5FFE)
    assert backend.register(0x5FFF, combo)
    backend.unregister(0x5FFF)
    app.processEvents()


def test_malformed_key_map_preserves_existing_binding(manager):
    m, backend, _ = manager
    m.apply(config(toggle="Ctrl+Alt+U"))
    before = dict(backend.registered)
    with pytest.raises(ValueError):
        m.apply({"enabled": True, "keys": []})
    assert backend.registered == before
