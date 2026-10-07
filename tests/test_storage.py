import json
from dataclasses import replace

import pytest

import usagedesk.storage as storage
from usagedesk.launcher import LauncherEntry
from usagedesk.storage import ConfigError, ConfigStore, FutureSchema, parse_config


def value():
    return LauncherEntry("프로그램", r"C:\projects\run.cmd", r"C:\projects")


def test_roundtrip_backup_and_recovery_preserves_corrupt_original(tmp_path):
    store = ConfigStore(tmp_path)
    first = value()
    store.save([first])
    store.save([replace(first, name="변경")])
    assert store.load()[0].name == "변경"
    broken = b'{"schema_version":1,broken'
    store.path.write_bytes(broken)
    with pytest.raises(ConfigError):
        store.load()
    with pytest.raises(ConfigError):
        store.save([])
    assert store.path.read_bytes() == broken
    assert store.can_recover()
    assert store.recover() == [first]
    assert next(tmp_path.glob("*.corrupt")).read_bytes() == broken


def test_future_schema_cannot_be_replaced_by_old_backup(tmp_path):
    store = ConfigStore(tmp_path)
    store.save([value()])
    store.save([])
    raw = b'{"schema_version":2,"programs":[]}'
    store.path.write_bytes(raw)
    with pytest.raises(FutureSchema):
        store.load()
    assert not store.can_recover()
    for action in (lambda: store.save([]), store.recover):
        with pytest.raises(ConfigError):
            action()
    assert store.path.read_bytes() == raw


def test_atomic_replace_failure_keeps_original_and_removes_temp(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.save([value()])
    original = store.path.read_bytes()
    real_replace = storage.os.replace

    def fail_on_primary(source, target):
        if target == store.path:
            raise PermissionError("test write failure")
        return real_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", fail_on_primary)
    with pytest.raises(PermissionError):
        store.save([])
    assert store.path.read_bytes() == original
    assert store.backup.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_restore_failure_remains_blocked(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.save([value()])
    store.save([])
    store.path.write_bytes(b"broken")
    with pytest.raises(ConfigError):
        store.load()

    def denied(*_):
        raise PermissionError("test restore failure")

    monkeypatch.setattr(storage, "atomic_write", denied)
    with pytest.raises(PermissionError):
        store.recover()
    assert store.blocked
    assert store.path.read_bytes() == b"broken"


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"{}",
        b'{"schema_version":true,"programs":[]}',
        b'{"schema_version":1,"programs":false}',
        b'{"schema_version":1,"schema_version":1,"programs":[]}',
        b'{"schema_version":1,"programs":[],"token":"sentinel-secret"}',
        b"\xff",
        b"[" * 10000,
    ],
)
def test_invalid_config_rejected(raw):
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_duplicate_ids_and_oversize_list_rejected():
    item = value().to_dict()
    for items in ([item, item], [value().to_dict() for _ in range(101)]):
        with pytest.raises(ConfigError):
            parse_config(json.dumps({"schema_version": 1, "programs": items}).encode())


def test_external_corruption_blocks_subsequent_save(tmp_path):
    store = ConfigStore(tmp_path)
    store.save([value()])
    store.path.write_bytes(b"broken")
    with pytest.raises(ConfigError):
        store.save([])
    assert store.blocked
    assert store.path.read_bytes() == b"broken"


def test_future_schema_written_after_failure_cannot_be_restored(tmp_path):
    store = ConfigStore(tmp_path)
    store.save([value()])
    store.save([])
    store.path.write_bytes(b"broken")
    with pytest.raises(ConfigError):
        store.load()
    future = b'{"schema_version":2,"programs":[]}'
    store.path.write_bytes(future)
    with pytest.raises(ConfigError):
        store.recover()
    assert store.path.read_bytes() == future
