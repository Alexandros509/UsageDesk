import json
from dataclasses import replace

import pytest

from usagedesk.launcher import Launcher, LauncherEntry, uses_file_association, validate_web_link
from usagedesk.storage import ConfigStore


@pytest.mark.parametrize('url', [
    'https://example.com/path?q=hello%20world&x=1#part',
    'http://localhost:3000/index.html',
    'HTTPS://example.com/download.exe?x=1&y=(2)',
    'https://example.com/한글',
    'http://[::1]:8080/',
])
def test_web_link_roundtrip_and_launch_does_not_invoke_shell(tmp_path, monkeypatch, url):
    entry = LauncherEntry('Website', url, '')
    store = ConfigStore(tmp_path)
    local = LauncherEntry('Existing file', r'C:\apps\run.cmd', r'C:\apps')
    store.save([local, entry])
    assert store.load() == [local, entry]
    assert json.loads(store.path.read_text('utf-8'))['schema_version'] == 1
    assert uses_file_association(url)
    opened = []
    monkeypatch.setattr('usagedesk.launcher.os.startfile', lambda *args: opened.append(args))
    def unexpected(*args, **kwargs):
        pytest.fail('A web link must not invoke a shell, runtime or file association launcher')
    monkeypatch.setattr('usagedesk.launcher.subprocess.Popen', unexpected)
    monkeypatch.setattr('usagedesk.launcher.open_associated', unexpected)
    launcher = Launcher()
    assert launcher.start(entry) is None
    assert launcher.start(entry) is None  # A shared browser cannot be tracked per URL.
    assert opened == [(url, 'open')] * 2
    assert not launcher.processes
    assert entry.id in launcher.poll()


@pytest.mark.parametrize('url', [
    'https://', 'https:example.com', 'https:///example.com',
    'javascript:alert(1)', 'file:///C:/apps/run.exe', 'ftp://example.com',
    'https://example.com/hello world', 'https://example.com/\npath',
    'https://example.com/\x00', 'https://user:password@example.com/',
    'https://example.com:99999', 'https://example.com:not-a-port',
    'https://example.com\\@other.example', 'https://[::1',
    'https://example.com/' + 'a' * 8192,
])
def test_invalid_web_links_rejected(url):
    with pytest.raises(ValueError):
        validate_web_link(url)
    with pytest.raises(ValueError):
        LauncherEntry('Bad URL', url, '').validate()


@pytest.mark.parametrize('changes', [
    {'arguments': ('ignored',)}, {'working_directory': r'C:\apps'},
    {'console_mode': 'hidden'},
    {'working_directory': None},
])
def test_web_link_rejects_inapplicable_file_options(changes):
    with pytest.raises(ValueError):
        replace(LauncherEntry('Web', 'https://example.com', ''), **changes).validate()


def test_browser_launch_failure_is_reported_without_success_status(monkeypatch):
    def fail(*args):
        raise OSError('No browser association')
    monkeypatch.setattr('usagedesk.launcher.os.startfile', fail)
    entry = LauncherEntry('Website', 'https://example.com', '')
    launcher = Launcher()
    with pytest.raises(OSError):
        launcher.start(entry)
    assert entry.id not in launcher.poll()


def test_editing_file_to_web_link_and_back_updates_tracking_state(monkeypatch):
    from types import SimpleNamespace

    entry = LauncherEntry('Starter', r'C:\apps\app.exe', r'C:\apps')
    launcher = Launcher()
    old = SimpleNamespace(pid=10, poll=lambda: 0)
    current = SimpleNamespace(pid=11, poll=lambda: None)
    launcher.processes[entry.id] = [old]
    monkeypatch.setattr('usagedesk.launcher.os.startfile', lambda *args: None)
    monkeypatch.setattr('usagedesk.launcher.validate_files', lambda item: item.validate())
    web = replace(entry, script_path='https://example.com', working_directory='')
    assert launcher.start(web) is None
    assert '프로세스 추적 불가' in launcher.poll()[entry.id]
    monkeypatch.setattr('usagedesk.launcher.runtime_command', lambda item: [item.script_path])
    monkeypatch.setattr('usagedesk.launcher.subprocess.Popen', lambda *a, **kw: current)
    assert launcher.start(entry) == 11
    assert entry.id not in launcher.untracked
    assert '실행 프로세스 추적 중' in launcher.poll()[entry.id]
