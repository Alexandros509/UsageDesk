import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from usagedesk.launcher import (
    Launcher,
    LauncherEntry,
    command_line,
    local_path,
    system_cmd,
)


def entry(**changes):
    return replace(
        LauncherEntry("시험", r"C:\개발 프로젝트\run.cmd", r"C:\개발 프로젝트"), **changes
    )


@pytest.mark.parametrize(
    "bad", ['"', "&", "|", "<", ">", "^", "%", "!", "(", ")", "\n", "\r", "\0", "\t"]
)
def test_shell_metacharacters_rejected(bad):
    for changes in [
        dict(arguments=("hello" + bad,)),
        dict(script_path=f"C:\\a{bad}.cmd"),
        dict(working_directory=f"C:\\a{bad}"),
    ]:
        with pytest.raises(ValueError):
            entry(**changes).validate()


@pytest.mark.parametrize(
    "path",
    [
        r"\\server\share\a.cmd",
        r"\\?\C:\a.cmd",
        r"\\.\C:\a.cmd",
        r"C:a.cmd",
        "a.cmd",
        "https://a/a.cmd",
        r"C:\a.cmd:payload",
        r"C:\NUL.cmd",
        r"C:\a.\b.cmd",
        r"C:\a \b.cmd",
        r"C:\a\..\b.cmd",
        r"C:\a\*\b.cmd",
        r"C:\a\\b.cmd",
    ],
)
def test_nonlocal_and_ambiguous_paths_rejected(path):
    with pytest.raises(ValueError):
        local_path(path, "시험 경로")


def test_argument_contract_and_length():
    value = entry(arguments=("한글 공백", "", "C:\\data\\"))
    value.validate()
    assert '"한글 공백" "" "C:\\data\\"' in command_line(value, r"C:\Windows\System32\cmd.exe")
    with pytest.raises(ValueError):
        command_line(entry(arguments=("x" * 1000,) * 9), system_cmd())
    with pytest.raises(ValueError):
        entry(arguments=("x",) * 65).validate()


@pytest.mark.skipif(os.name != "nt", reason="Windows actual process test")
@pytest.mark.parametrize("extension", [".cmd", ".bat"])
def test_real_cmd_unicode_cwd_arguments_and_exit_code(tmp_path, extension):
    folder = tmp_path / "개발 프로젝트"
    folder.mkdir()
    script = folder / ("시험 실행" + extension)
    script.write_bytes(
        b"@echo off\r\nchcp 65001 >nul\r\n>result.txt echo %CD%\r\n>>result.txt echo [%~1]\r\n>>result.txt echo [%~2]\r\nexit /b 7\r\n"
    )
    value = LauncherEntry("실제 시험", str(script), str(folder), ("한글 argument", ""), "hidden")
    launcher = Launcher()
    launcher.start(value)
    assert launcher.processes[value.id][0].wait(timeout=10) == 7
    assert (folder / "result.txt").read_text(encoding="utf-8").splitlines() == [
        str(folder),
        "[한글 argument]",
        "[]",
    ]
    assert "7" in launcher.poll()[value.id]


@pytest.mark.skipif(os.name != "nt", reason="Windows actual process test")
def test_duplicate_wrapper_rejected_and_allowed_after_exit(tmp_path):
    script = tmp_path / "delay.cmd"
    # The helper command is inside the user-controlled test CMD, never injected into arguments.
    script.write_text(
        '@echo off\r\n"%SystemRoot%\\System32\\ping.exe" -n 2 127.0.0.1 >nul\r\nexit /b 0\r\n',
        encoding="ascii",
    )
    value = LauncherEntry("지연", str(script), str(tmp_path), console_mode="hidden")
    launcher = Launcher()
    launcher.start(value)
    with pytest.raises(ValueError, match="이미 추적"):
        launcher.start(value)
    launcher.processes[value.id][0].wait(timeout=10)
    launcher.start(value)
    launcher.processes[value.id][0].wait(timeout=10)


def test_missing_script_never_starts(tmp_path):
    launcher = Launcher()
    with pytest.raises((OSError, ValueError)):
        launcher.start(LauncherEntry("누락", str(tmp_path / "missing.cmd"), str(tmp_path)))
    assert launcher.processes == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows actual lifetime test")
def test_child_survives_parent_exit(tmp_path):
    script = tmp_path / "survive.cmd"
    script.write_text(
        '@echo off\r\n"%SystemRoot%\\System32\\ping.exe" -n 2 127.0.0.1 >nul\r\n>survived.txt echo survived\r\n',
        encoding="ascii",
    )
    value = LauncherEntry("수명", str(script), str(tmp_path), console_mode="hidden")
    helper = tmp_path / "parent.py"
    helper.write_text(
        "from usagedesk.launcher import Launcher, LauncherEntry\nimport json\nentry = LauncherEntry.from_dict(json.loads("
        + repr(json.dumps(value.to_dict()))
        + "))\nLauncher().start(entry)\n",
        encoding="utf-8",
    )
    result = subprocess.run([sys.executable, str(helper)], timeout=10, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    deadline = time.monotonic() + 5
    while not (tmp_path / "survived.txt").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (tmp_path / "survived.txt").read_text().strip() == "survived"


def test_system_cmd_ignores_comspec(monkeypatch):
    monkeypatch.setenv("COMSPEC", r"C:\untrusted\cmd.exe")
    assert system_cmd().lower() != os.environ["COMSPEC"].lower()
    assert Path(system_cmd()).is_file()


@pytest.mark.parametrize("mode", ["close_on_exit", "keep_open"])
def test_real_console_modes_exit_with_explicit_exit(tmp_path, monkeypatch, mode):
    script = tmp_path / "mode.cmd"
    script.write_bytes(b"@echo off\r\n>mode.txt echo ok\r\nexit 0\r\n")
    original_popen = subprocess.Popen

    def hidden_test_console(*args, **kwargs):
        assert kwargs["creationflags"] == subprocess.CREATE_NEW_CONSOLE
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startup
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", hidden_test_console)
    value = LauncherEntry("콘솔", str(script), str(tmp_path), console_mode=mode)
    launcher = Launcher()
    launcher.start(value)
    assert launcher.processes[value.id][0].wait(timeout=10) == 0
    assert (tmp_path / "mode.txt").read_text().strip() == "ok"
