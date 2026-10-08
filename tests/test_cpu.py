from usagedesk.cpu import CpuMonitor, Process


class Backend:
    def __init__(self):
        self.rows = {10: Process(10, 1, 'claude.exe'), 11: Process(11, 10, 'helper.exe')}
        self.values = {10: (100, 1.0), 11: (101, 2.0)}

    def list(self):
        return self.rows

    def times(self, pid):
        return self.values.get(pid)


def test_cpu_tree_normalization_and_shared_pid_not_double_counted():
    backend = Backend()
    clock = [0]
    monitor = CpuMonitor(backend, cores=4, clock=lambda: clock[0])
    assert monitor.sample({'program': {10, 11}})['program'].state == 'sampling'
    clock[0] = 2
    backend.values = {10: (100, 2.0), 11: (101, 3.0)}
    readings = monitor.sample({'program': {10, 11}})
    assert readings['program'].percent == 25
    assert readings['claude'].percent == 25
    assert readings['codex'].state == 'not_running'


def test_descendant_survives_parent_exit_but_reused_pid_does_not():
    backend = Backend()
    clock = [0]
    monitor = CpuMonitor(backend, cores=2, clock=lambda: clock[0])
    monitor.sample({'program': {10}}, ai=False)
    del backend.rows[10]
    del backend.values[10]
    backend.values[11] = (101, 3.0)
    clock[0] = 2
    assert monitor.sample({'program': set()}, ai=False)['program'].percent == 25
    backend.values[11] = (200, 5.0)
    clock[0] = 4
    assert monitor.sample({'program': set()}, ai=False)['program'].state == 'untracked'


def test_inaccessible_or_untracked_is_not_fake_zero():
    backend = Backend()
    backend.values[11] = None
    monitor = CpuMonitor(backend)
    result = monitor.sample({'html': None, 'program': {10}})
    assert result['html'].state == 'untracked'
    assert result['program'].state == 'unavailable'
    assert result['program'].percent is None


def test_disabled_provider_and_removed_program_are_not_retained():
    monitor = CpuMonitor(Backend())
    monitor.sample({'program': {10}})
    assert monitor.sample({}, ai=False) == {}
    assert monitor.known == {}
    monitor.reset()
    assert not monitor.previous and monitor.last is None


def test_cpu_sampler_windows_reads_only_process_counters():
    import os
    import time

    from usagedesk.cpu import WindowsProcesses
    backend = WindowsProcesses()
    rows = backend.list()
    assert os.getpid() in rows
    assert backend.times(os.getpid())[0] > 0
    monitor = CpuMonitor(backend)
    assert monitor.sample({'self': {os.getpid()}}, ai=False)['self'].percent is None
    until = time.monotonic() + .15
    while time.monotonic() < until:
        pass
    value = monitor.sample({'self': {os.getpid()}}, ai=False)['self']
    assert value.state == 'ready' and 0 <= value.percent <= 100


def test_old_orphan_is_not_child_of_reused_parent_pid():
    backend = Backend()
    backend.values[10] = (300, 1)
    monitor = CpuMonitor(backend)
    monitor.sample({'program': {10}}, ai=False)
    assert monitor.known['program'] == {(10, 300)}
