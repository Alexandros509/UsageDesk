"""Opt-in local process CPU sampling. Never reads command lines or credentials."""
import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    name: str


@dataclass(frozen=True)
class Reading:
    percent: float | None
    state: str


class WindowsProcesses:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k = self.kernel
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]

    def list(self):
        class Entry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                        ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                        ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                        ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                        ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260)]
        k = self.kernel
        for name in ('Process32FirstW', 'Process32NextW'):
            getattr(k, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        handle = k.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError('Process enumeration failed')
        try:
            entry = Entry()
            entry.size = ctypes.sizeof(entry)
            found = k.Process32FirstW(handle, ctypes.byref(entry))
            result = {}
            while found:
                result[entry.pid] = Process(entry.pid, entry.parent, entry.name.lower())
                found = k.Process32NextW(handle, ctypes.byref(entry))
            return result
        finally:
            k.CloseHandle(handle)

    def times(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited, kernel, user = [wintypes.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle, *[ctypes.byref(v) for v in (created, exited, kernel, user)]):
                return None
            def ticks(value):
                return (value.dwHighDateTime << 32) | value.dwLowDateTime
            return ticks(created), (ticks(kernel) + ticks(user)) / 10_000_000
        finally:
            self.kernel.CloseHandle(handle)


class CpuMonitor:
    """Normalize process-tree CPU to a machine-wide 0–100% scale."""
    def __init__(self, backend=None, cores=None, clock=time.monotonic):
        self.backend = backend or WindowsProcesses()
        self.cores = max(1, cores or os.cpu_count() or 1)
        self.clock = clock
        self.previous = {}
        self.known = {}
        self.last = None

    def reset(self):
        self.previous.clear()
        self.known.clear()
        self.last = None

    def sample(self, program_roots, ai=True):
        now = self.clock()
        elapsed = None if self.last is None else now - self.last
        self.last = now
        try:
            processes = self.backend.list()
        except OSError:
            self.reset()
            return {key: Reading(None, 'unavailable') for key in
                    [*program_roots, *(['claude', 'codex', 'grok'] if ai else [])]}
        groups = {key: set(roots or ()) for key, roots in program_roots.items()}
        if ai:
            for key in ('claude', 'codex', 'grok'):
                groups[key] = {pid for pid, p in processes.items() if p.name == key + '.exe'}
        values = {}
        def times(pid):
            if pid not in values:
                values[pid] = self.backend.times(pid)
            return values[pid]
        readings, known = {}, {}
        for key, roots in groups.items():
            if key in program_roots and program_roots[key] is None:
                readings[key] = Reading(None, 'untracked')
                continue
            members = roots & processes.keys()
            # Retain observed descendants after a short-lived launcher exits;
            # process creation times prevent counting a reused PID.
            for pid, created in self.known.get(key, set()):
                value = times(pid) if pid in processes else None
                if value is not None and value[0] == created:
                    members.add(pid)
            while True:
                children = set()
                for pid, process in processes.items():
                    if process.parent not in members or pid in members:
                        continue
                    child, parent = times(pid), times(process.parent)
                    if child is None or parent is None or child[0] >= parent[0]:
                        children.add(pid)
                if children <= members:
                    break
                members |= children
            if not members:
                readings[key] = Reading(None, 'not_running' if key not in program_roots else 'untracked')
                continue
            samples = [times(pid) for pid in members]
            known[key] = {(pid, values[pid][0]) for pid in members if values[pid] is not None}
            if any(value is None for value in samples):
                readings[key] = Reading(None, 'unavailable')
                continue
            delta = 0.0
            warm = False
            for pid in members:
                created, total = values[pid]
                prior = self.previous.get((pid, created))
                if prior is None:
                    warm = True
                else:
                    delta += max(0, total - prior)
            if warm or elapsed is None or elapsed <= 0:
                readings[key] = Reading(None, 'sampling')
            else:
                readings[key] = Reading(min(100.0, delta / elapsed / self.cores * 100), 'ready')
        self.known = known
        self.previous = {(pid, val[0]): val[1] for pid, val in values.items() if val is not None}
        return readings
