from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from .i18n import tr
from .launcher import LauncherEntry

MAX_BYTES = 1024 * 1024


class ConfigError(ValueError):
    pass


class FutureSchema(ConfigError):
    pass


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(tr('중복 설정 필드가 있습니다.'))
        result[key] = value
    return result


def parse_config(raw: bytes) -> list[LauncherEntry]:
    if len(raw) > MAX_BYTES:
        raise ConfigError(tr('설정 파일은 최대 1MiB입니다.'))
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_fields)
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
            raise ValueError(tr('설정 버전이 올바르지 않습니다.'))
        if data["schema_version"] > 1:
            raise FutureSchema(tr('더 새로운 앱의 설정입니다. 최신 앱으로 열어 주세요.'))
        if data["schema_version"] != 1 or set(data) != {"schema_version", "programs"}:
            raise ValueError(tr('설정 구조가 올바르지 않습니다.'))
        if not isinstance(data["programs"], list) or len(data["programs"]) > 100:
            raise ValueError(tr('프로그램 목록은 최대 100개입니다.'))
        entries = [LauncherEntry.from_dict(item) for item in data["programs"]]
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError(tr('중복 프로그램 ID가 있습니다.'))
        return entries
    except FutureSchema:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError) as exc:
        raise ConfigError(
            tr('설정이 손상되었거나 지원하지 않는 필드가 있습니다. 원본을 보존했습니다.')
        ) from exc


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def bounded_read(path: Path) -> bytes:
    with path.open("rb") as stream:
        result = stream.read(MAX_BYTES + 1)
    if len(result) > MAX_BYTES:
        raise ConfigError(tr('설정 파일은 최대 1MiB입니다.'))
    return result


class ConfigStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "config.json"
        self.backup = directory / "config.json.bak"
        self.blocked = False
        self.future = False

    def load(self) -> list[LauncherEntry]:
        try:
            entries = parse_config(bounded_read(self.path)) if self.path.exists() else []
            self.blocked = self.future = False
            return entries
        except (OSError, ConfigError) as exc:
            self.blocked = True
            self.future = isinstance(exc, FutureSchema)
            raise

    def save(self, entries: list[LauncherEntry]) -> None:
        if self.blocked:
            raise ConfigError(tr('설정 복구 전에는 변경할 수 없습니다.'))
        content = json.dumps(
            {"schema_version": 1, "programs": [e.to_dict() for e in entries]},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        parse_config(content)
        if self.path.exists():
            try:
                old = bounded_read(self.path)
                parse_config(old)
            except (OSError, ConfigError) as exc:
                self.blocked = True
                self.future = isinstance(exc, FutureSchema)
                raise
            atomic_write(self.backup, old)
        atomic_write(self.path, content)

    def can_recover(self) -> bool:
        if not self.blocked or self.future:
            return False
        # The file may have changed externally since the initial failed load.
        try:
            if self.path.exists():
                parse_config(bounded_read(self.path))
        except FutureSchema:
            self.future = True
            return False
        except ConfigError:
            pass
        except OSError:
            return False
        try:
            parse_config(bounded_read(self.backup))
            return True
        except (OSError, ConfigError):
            return False

    def recover(self) -> list[LauncherEntry]:
        if not self.can_recover():
            raise ConfigError(tr('복구 가능한 백업이 없습니다. 원본은 변경하지 않았습니다.'))
        raw = bounded_read(self.backup)
        entries = parse_config(raw)
        if self.path.exists():
            # Stream the original without the parsing size limit: never truncate evidence.
            preserved = self.directory / f"config.{uuid4().hex}.corrupt"
            with self.path.open("rb") as source, preserved.open("xb") as target:
                while chunk := source.read(65536):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        atomic_write(self.path, raw)
        self.blocked = self.future = False
        return entries
