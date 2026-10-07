import json
import subprocess
import sys
from pathlib import Path

import pytest

from usagedesk.launcher import SUPPORTED_EXTENSIONS, Launcher, LauncherEntry, runtime_command


@pytest.mark.parametrize("suffix", sorted(SUPPORTED_EXTENSIONS))
def test_supported_starters_roundtrip(suffix):
    entry = LauncherEntry("Starter", "C:\\Apps\\start" + suffix, "C:\\Apps")
    assert LauncherEntry.from_dict(json.loads(json.dumps(entry.to_dict()))) == entry


@pytest.mark.parametrize("suffix", [".html", ".PDF", ".txt", ".custom", ""])
def test_general_files_use_association_without_command_interpolation(tmp_path, monkeypatch, suffix):
    path = tmp_path / ("문서 & 자료" + suffix)
    path.write_text("test", encoding="utf-8")
    calls = []
    monkeypatch.setattr("usagedesk.launcher.open_associated", lambda entry: calls.append(entry))
    entry = LauncherEntry("File", str(path), str(tmp_path))
    assert Launcher().start(entry) is None
    assert calls == [entry]


def test_document_options_and_missing_association_are_clear(tmp_path, monkeypatch):
    import pywintypes
    from win32com.shell import shell

    from usagedesk.launcher import open_associated
    entry = LauncherEntry("HTML", str(tmp_path / "index.html"), str(tmp_path))
    from dataclasses import replace
    with pytest.raises(ValueError, match="기본 콘솔 모드"):
        replace(entry, console_mode="hidden").validate()
    with pytest.raises(ValueError, match="추가 인수"):
        replace(entry, arguments=("ignored",)).validate()

    def missing(**kwargs):
        raise pywintypes.error(1155, "ShellExecuteEx", "No association")
    monkeypatch.setattr(shell, "ShellExecuteEx", missing)
    with pytest.raises(ValueError, match="기본 앱"):
        open_associated(entry)


@pytest.mark.parametrize("suffix,host", [(".ps1", "powershell.exe"), (".vbs", "cscript.exe"),
                                        (".js", "cscript.exe"), (".msc", "mmc.exe"),
                                        (".hta", "mshta.exe")])
def test_script_hosts_pass_literal_argument_arrays(suffix, host):
    entry = LauncherEntry("Starter", "C:\\Apps (x86)\\file" + suffix, "C:\\Apps (x86)",
                          ('x & y', 'quote"here', '10%'), "hidden")
    entry.validate()
    args = runtime_command(entry)
    assert Path(args[0]).name == host
    assert args[-4:] == [entry.script_path, *entry.arguments]
    assert "-ExecutionPolicy" not in args


@pytest.mark.parametrize("suffix", [".exe", ".py"])
def test_real_native_and_python_preserve_special_arguments(tmp_path, monkeypatch, suffix):
    folder = tmp_path / "프로젝트 (시험)"
    folder.mkdir()
    script = folder / "starter.py"
    script.write_text("import json,sys,pathlib\npathlib.Path('result.json').write_text("
                      "json.dumps(sys.argv[1:]),encoding='utf-8')\n", encoding="utf-8")
    monkeypatch.setattr("usagedesk.launcher.shutil.which", lambda name: sys.executable)
    args = ('한글 공백', 'x & y', 'quote"end', '10%', 'C:\\trailing\\')
    entry = LauncherEntry("실행", str(script) if suffix == ".py" else sys.executable,
                          str(folder), args if suffix == ".py" else (str(script), *args), "hidden")
    launcher = Launcher()
    launcher.start(entry)
    assert launcher.processes[entry.id][0].wait(timeout=10) == 0
    assert json.loads((folder / "result.json").read_text(encoding="utf-8")) == list(args)


def test_python_prefers_project_venv_and_missing_runtime_is_clear(tmp_path, monkeypatch):
    entry = LauncherEntry("Python", str(tmp_path / "start.py"), str(tmp_path))
    monkeypatch.setattr("usagedesk.launcher.shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="실행기를 찾을 수"):
        runtime_command(entry)
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"test-only")
    assert runtime_command(entry)[0] == str(interpreter)


def test_associated_no_process_handle_is_honest(tmp_path, monkeypatch):
    link = tmp_path / "app.lnk"
    link.write_bytes(b"test-only")
    calls = []
    monkeypatch.setattr("usagedesk.launcher.open_associated", lambda entry: calls.append(entry))
    entry = LauncherEntry("Shortcut", str(link), str(tmp_path))
    launcher = Launcher()
    assert launcher.start(entry) is None
    assert calls == [entry]
    assert "추적 불가" in launcher.poll()[entry.id]
    assert not launcher.processes


def test_shell_execute_uses_registered_file_without_cmd(tmp_path, monkeypatch):
    from win32com.shell import shell

    from usagedesk.launcher import open_associated
    calls = []
    monkeypatch.setattr(shell, "ShellExecuteEx", lambda **kw: calls.append(kw) or {})
    entry = LauncherEntry("Shortcut", str(tmp_path / "app.lnk"), str(tmp_path), ("space arg",))
    assert open_associated(entry) is None
    assert calls[0]["lpFile"] == entry.script_path
    assert calls[0]["lpVerb"] == "open"
    assert calls[0]["lpParameters"] == subprocess.list2cmdline(entry.arguments)
