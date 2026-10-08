from __future__ import annotations

import ctypes
import ntpath
import os
import re
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from uuid import UUID, uuid4

from .i18n import tr

MODES = {
    "close_on_exit": tr('완료 후 콘솔 닫기'),
    "keep_open": tr('실행 후 콘솔 유지'),
    "hidden": tr('콘솔 숨김'),
}
FORBIDDEN = set('"&|<>^%!()')


BATCH = {".cmd", ".bat"}
ASSOCIATED = {".lnk", ".url", ".appref-ms", ".ahk", ".wsh"}
SCRIPT_HOST = {".vbs", ".vbe", ".js", ".jse", ".wsf"}
EXECUTABLE_EXTENSIONS = BATCH | SCRIPT_HOST | {
    ".exe", ".com", ".py", ".pyw", ".ps1", ".jar", ".msc", ".hta",
}
DOCUMENT_EXTENSIONS = {".html", ".htm", ".pdf", ".txt", ".md", ".docx", ".xlsx", ".pptx",
                       ".csv", ".json", ".png", ".jpg", ".jpeg", ".svg", ".mp3", ".mp4", ".zip"}
SUPPORTED_EXTENSIONS = EXECUTABLE_EXTENSIONS | ASSOCIATED | DOCUMENT_EXTENSIONS
FILE_FILTER = (tr('프로그램·문서·미디어 (')
               + " ".join("*" + ext for ext in sorted(SUPPORTED_EXTENSIONS)) + tr(');;모든 파일 (*)'))


def uses_file_association(path):
    return PureWindowsPath(path).suffix.lower() not in EXECUTABLE_EXTENSIONS


def safe_text(value: str, label: str, maximum: int = 1024, *, shell=True) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(tr('{p0}: 문자열 길이를 확인하세요 (최대 {p1}자).', p0=label, p1=maximum))
    if any(ord(c) < 32 or ord(c) == 127 or (shell and c in FORBIDDEN) for c in value):
        raise ValueError(tr('{p0}: 제어 문자 및 " & | < > ^ % ! ( ) 문자는 지원하지 않습니다.', p0=label))
    return value


def local_path(value: str, label: str, *, shell=True) -> PureWindowsPath:
    safe_text(value, label, shell=shell)
    if '"' in value:
        raise ValueError(tr('{p0}: 경로에 큰따옴표를 사용할 수 없습니다.', p0=label))
    value = value.replace("/", "\\")
    if not re.match(r"^[A-Za-z]:\\", value) or ":" in value[2:]:
        raise ValueError(tr('{p0}: 로컬 드라이브의 절대 경로만 허용합니다.', p0=label))
    parts = value[3:].split("\\")
    if parts == [""]:
        parts = []  # Drive root is a valid working directory.
    if any(not p or p in {".", ".."} or p.endswith((" ", ".")) for p in parts):
        raise ValueError(tr('{p0}: 빈 경로 요소, 상대 요소, 끝 공백·마침표는 허용하지 않습니다.', p0=label))
    if any(c in value for c in "*?") or any(ntpath.isreserved(p) for p in parts):
        raise ValueError(tr('{p0}: 장치 이름이나 와일드카드는 허용하지 않습니다.', p0=label))
    return PureWindowsPath(value)


@dataclass(frozen=True)
class LauncherEntry:
    name: str
    script_path: str
    working_directory: str
    arguments: tuple[str, ...] = ()
    console_mode: str = "close_on_exit"
    favorite: bool = False
    allow_multiple: bool = False
    id: str = field(default_factory=lambda: str(uuid4()))

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise ValueError(tr('프로그램 이름은 1~100자로 입력하세요.'))
        if any(ord(c) < 32 for c in self.name):
            raise ValueError(tr('이름에는 제어 문자를 사용할 수 없습니다.'))
        if not isinstance(self.id, str) or str(UUID(self.id)) != self.id:
            raise ValueError(tr('프로그램 ID가 올바르지 않습니다.'))
        if not isinstance(self.script_path, str):
            raise ValueError(tr('실행 파일 경로를 확인하세요.'))
        suffix = PureWindowsPath(self.script_path).suffix.lower()
        local_path(self.script_path, tr('파일'), shell=suffix in BATCH)
        local_path(self.working_directory, tr('작업 폴더'), shell=suffix in BATCH)
        if not isinstance(self.console_mode, str) or self.console_mode not in MODES:
            raise ValueError(tr('콘솔 모드가 올바르지 않습니다.'))
        if self.console_mode == "keep_open" and suffix not in BATCH | {".ps1"}:
            raise ValueError(tr('콘솔 유지는 CMD·BAT·PowerShell에서 지원합니다.'))
        if uses_file_association(self.script_path) and self.console_mode != "close_on_exit":
            raise ValueError(tr('바로가기·파일 연결 실행은 기본 콘솔 모드를 사용하세요.'))
        if uses_file_association(self.script_path) and suffix not in {".lnk", ".ahk"} and self.arguments:
            raise ValueError(tr('이 파일 형식은 추가 인수를 지원하지 않습니다.'))
        if type(self.favorite) is not bool or type(self.allow_multiple) is not bool:
            raise ValueError(tr('즐겨찾기·중복 실행 값이 올바르지 않습니다.'))
        if not isinstance(self.arguments, (tuple, list)) or len(self.arguments) > 64:
            raise ValueError(tr('인수는 최대 64개입니다.'))
        for value in self.arguments:
            safe_text(value, tr('인수'), shell=suffix in BATCH)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> LauncherEntry:
        if not isinstance(value, dict):
            raise ValueError(tr('프로그램 설정은 객체여야 합니다.'))
        allowed = set(cls.__dataclass_fields__)
        if set(value) != allowed:
            raise ValueError(tr('프로그램 설정의 필드가 올바르지 않습니다.'))
        if not isinstance(value["arguments"], list):
            raise ValueError(tr('인수 목록이 올바르지 않습니다.'))
        entry = cls(**{**value, "arguments": tuple(value["arguments"])})
        entry.validate()
        return entry


def validate_files(entry: LauncherEntry) -> None:
    entry.validate()
    if os.name != "nt":
        raise OSError(tr('Windows에서만 실행할 수 있습니다.'))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    for text in (entry.script_path, entry.working_directory):
        path = Path(text)
        if kernel.GetDriveTypeW(path.anchor) not in {2, 3, 6}:
            raise ValueError(
                tr('로컬 드라이브만 실행할 수 있습니다. 네트워크 경로는 지원하지 않습니다.')
            )
        for component in (path, *path.parents):
            info = component.lstat()
            if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(tr('연결·재분석 경로는 지원하지 않습니다. 실제 경로를 지정하세요.'))
    if not Path(entry.script_path).is_file():
        raise ValueError(tr('실행 파일을 찾을 수 없습니다. 경로를 다시 지정하세요.'))
    if not Path(entry.working_directory).is_dir():
        raise ValueError(tr('작업 폴더를 찾을 수 없습니다. 경로를 다시 지정하세요.'))


def system_cmd() -> str:
    if os.name != "nt":
        raise OSError(tr('Windows 전용 기능입니다.'))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return str(Path(buffer.value) / "cmd.exe")


def command_line(entry: LauncherEntry, executable: str) -> str:
    entry.validate()
    if Path(entry.script_path).suffix.lower() not in BATCH:
        raise ValueError(tr('CMD 명령 문자열은 CMD·BAT에서만 사용합니다.'))
    switch = "/k" if entry.console_mode == "keep_open" else "/c"
    payload = " ".join(f'"{value}"' for value in (entry.script_path, *entry.arguments))
    command = f'"{executable}" /d /v:off /s {switch} "{payload}"'
    if len(command.encode("utf-16-le")) // 2 > 8000:
        raise ValueError(tr('전체 실행 명령이 너무 깁니다 (최대 8000자).'))
    return command


def runtime_command(entry: LauncherEntry) -> list[str]:
    """Native argument lists, never shell interpolation, for non-batch programs."""
    suffix = Path(entry.script_path).suffix.lower()
    system = Path(system_cmd()).parent

    def find_runtime(*names):
        for name in names:
            if found := shutil.which(name):
                return found
        raise ValueError(tr('실행기를 찾을 수 없습니다: ') + " / ".join(names))

    if suffix in {".exe", ".com"}:
        prefix = []
    elif suffix in {".py", ".pyw"}:
        binary = "pythonw.exe" if suffix == ".pyw" else "python.exe"
        candidates = [Path(entry.working_directory) / ".venv" / "Scripts" / binary,
                      Path(entry.script_path).parent / ".venv" / "Scripts" / binary]
        interpreter = next((str(p) for p in candidates if p.is_file()), None)
        prefix = [interpreter or find_runtime(
            "pyw.exe" if suffix == ".pyw" else "py.exe", binary)]
    elif suffix == ".ps1":
        prefix = [str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoProfile"]
        if entry.console_mode == "keep_open":
            prefix.append("-NoExit")
        prefix.append("-File")
    elif suffix in SCRIPT_HOST:
        prefix = [str(system / "cscript.exe"), "//Nologo"]
    elif suffix == ".jar":
        prefix = [find_runtime("java.exe"), "-jar"]
    elif suffix in {".msc", ".hta"}:
        prefix = [str(system / ("mmc.exe" if suffix == ".msc" else "mshta.exe"))]
    else:
        raise ValueError(tr('직접 실행 형식이 아닙니다.'))
    return [*prefix, entry.script_path, *entry.arguments]


class ShellProcess:
    """Close only the tracking handle, never the user's launched process."""
    def __init__(self, handle):
        import win32process
        self.handle = handle
        self.pid = win32process.GetProcessId(handle)
        self.code = None

    def poll(self):
        import win32event
        import win32process
        if self.handle is not None and win32event.WaitForSingleObject(self.handle, 0) == 0:
            self.code = win32process.GetExitCodeProcess(self.handle)
            self.handle.Close()
            self.handle = None
        return self.code

    def __del__(self):
        if self.handle is not None:
            self.handle.Close()


def open_associated(entry):
    import pythoncom
    import pywintypes
    from win32com.shell import shell, shellcon

    pythoncom.CoInitialize()
    try:
        result = shell.ShellExecuteEx(
            fMask=(shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_FLAG_NO_UI
                       | 0x00000100),  # SEE_MASK_NOASYNC (not exported by pywin32)
            lpVerb="open", lpFile=entry.script_path,
            lpParameters=subprocess.list2cmdline(entry.arguments),
            lpDirectory=entry.working_directory, nShow=1,
        )
        handle = result.get("hProcess")
        return ShellProcess(handle) if handle else None
    except pywintypes.error as exc:
        if exc.winerror in (31, 1155):
            raise ValueError(tr('이 파일을 열 기본 앱이 없습니다. Windows에서 기본 앱을 지정하세요.')) from None
        raise
    finally:
        pythoncom.CoUninitialize()


class Launcher:
    """Called by one serialized worker; never owns or terminates external children."""

    def __init__(self) -> None:
        self.processes: dict[str, list[subprocess.Popen | ShellProcess]] = {}
        self.untracked = set()

    def start(self, entry: LauncherEntry) -> int | None:
        validate_files(entry)
        active = [p for p in self.processes.get(entry.id, []) if p.poll() is None]
        if active and not entry.allow_multiple:
            raise ValueError(tr('이 항목의 실행 프로세스를 이미 추적 중입니다.'))
        suffix = Path(entry.script_path).suffix.lower()
        if uses_file_association(entry.script_path):
            proc = open_associated(entry)
            if proc is None:
                self.untracked.add(entry.id)
                return None
            self.untracked.discard(entry.id)
            self.processes[entry.id] = [*active, proc]
            return proc.pid
        executable = system_cmd() if suffix in BATCH else None
        command = command_line(entry, executable) if executable else runtime_command(entry)
        flags = (
            subprocess.CREATE_NO_WINDOW
            if entry.console_mode == "hidden"
            else subprocess.CREATE_NEW_CONSOLE
        )
        proc = subprocess.Popen(
            command,
            executable=executable,
            cwd=entry.working_directory,
            shell=False,
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL if entry.console_mode == "hidden" else None,
            stdout=subprocess.DEVNULL if entry.console_mode == "hidden" else None,
            stderr=subprocess.DEVNULL if entry.console_mode == "hidden" else None,
        )
        self.processes[entry.id] = [*active, proc]
        return proc.pid

    def poll(self) -> dict[str, str]:
        statuses = dict.fromkeys(self.untracked, tr('파일 연결로 실행 요청 완료 · 프로세스 추적 불가'))
        for entry_id, processes in self.processes.items():
            codes = [proc.poll() for proc in processes]
            running = sum(code is None for code in codes)
            if running:
                statuses[entry_id] = tr('실행 프로세스 추적 중 ({p0}개)', p0=running)
            elif codes and entry_id not in self.untracked:
                statuses[entry_id] = tr('종료 코드 {p0} · 하위 프로그램 상태 미확인', p0=codes[-1])
        return statuses
