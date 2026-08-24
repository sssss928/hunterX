"""Low-frequency, bounded runtime resource observations."""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import runtime_health
from platform_registry import platform_key_for_url
from task_registry import hunterx_tasks


@dataclass(frozen=True)
class RuntimeDiagnostics:
    hunterx_rss_bytes: int | None
    browser_rss_bytes: int | None
    hunterx_cpu_percent: float | None
    tab_count: int
    hunterx_task_count: int
    browser_action_count: int
    cdp_timeout_count: int
    reconnect_count: int
    recovery_count: int
    runtime_log_bytes: int | None
    owned_tab_platform_key: str
    same_platform_tab_count: int
    same_platform_extra_tab_count: int
    same_platform_extra_target_ids: tuple[str, ...]
    supports_same_browser_multi_tab: bool

    def fields(self) -> dict[str, Any]:
        return asdict(self)


def _process_metrics(pid: int | None) -> tuple[int | None, float | None]:
    if not pid:
        return None, None
    try:
        import psutil

        process = psutil.Process(int(pid))
        return int(process.memory_info().rss), float(process.cpu_percent(interval=None))
    except (ImportError, OSError, ValueError):
        return _windows_rss_bytes(pid), None


@lru_cache(maxsize=1)
def _windows_process_api() -> tuple[Any, Any, Any, Any] | None:
    """Bind Windows process APIs once; repeated ctypes classes leak metadata."""

    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        class ProcessEntry32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", wintypes.WPARAM),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = getattr(ctypes, "windll").kernel32
        psapi = getattr(ctypes, "windll").psapi
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry32),
        ]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry32),
        ]
        kernel32.Process32NextW.restype = wintypes.BOOL
        return ctypes, wintypes, ProcessMemoryCounters, ProcessEntry32
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _windows_rss_bytes(pid: int) -> int | None:
    """Read working-set RSS without making psutil a runtime dependency."""

    api = _windows_process_api()
    if api is None:
        return None
    ctypes, _wintypes, ProcessMemoryCounters, _ProcessEntry32 = api
    try:
        kernel32 = getattr(ctypes, "windll").kernel32
        psapi = getattr(ctypes, "windll").psapi
        process_query_limited_information = 0x1000
        process_vm_read = 0x0010
        handle = kernel32.OpenProcess(
            process_query_limited_information | process_vm_read,
            False,
            int(pid),
        )
        if not handle:
            return None
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                counters.cb,
            ):
                return None
            return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _windows_process_tree_pids(root_pid: int) -> set[int]:
    api = _windows_process_api()
    if api is None:
        return {root_pid}
    ctypes, wintypes, _ProcessMemoryCounters, ProcessEntry32 = api
    try:
        kernel32 = getattr(ctypes, "windll").kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = wintypes.HANDLE(-1).value
        if not snapshot or snapshot == invalid_handle:
            return {root_pid}
        parents: dict[int, set[int]] = {}
        try:
            entry = ProcessEntry32()
            entry.dwSize = ctypes.sizeof(entry)
            has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
            while has_entry:
                parents.setdefault(int(entry.th32ParentProcessID), set()).add(
                    int(entry.th32ProcessID)
                )
                has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snapshot)
        tree = {int(root_pid)}
        frontier = [int(root_pid)]
        while frontier:
            parent = frontier.pop()
            for child in parents.get(parent, ()):
                if child not in tree:
                    tree.add(child)
                    frontier.append(child)
        return tree
    except (AttributeError, OSError, TypeError, ValueError):
        return {root_pid}


def _process_tree_rss_bytes(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        import psutil

        root = psutil.Process(int(pid))
        processes = [root, *root.children(recursive=True)]
        values = [int(process.memory_info().rss) for process in processes]
    except (ImportError, OSError, ValueError):
        values = [
            value
            for process_pid in _windows_process_tree_pids(int(pid))
            if (value := _process_metrics(process_pid)[0]) is not None
        ]
    return sum(values) if values else None


def collect_runtime_diagnostics(
    session_manager: Any,
    health_supervisor: runtime_health.RuntimeHealthSupervisor,
) -> RuntimeDiagnostics:
    hunterx_rss, hunterx_cpu = _process_metrics(os.getpid())
    browser_pid = getattr(session_manager, "browser_pid", lambda: None)()
    browser_rss = _process_tree_rss_bytes(browser_pid)
    driver = getattr(session_manager, "driver", None)
    tabs = getattr(driver, "tabs", ()) if driver is not None else ()
    tab_count = len(tabs) if isinstance(tabs, (tuple, list)) else 0
    health = health_supervisor.snapshot()
    active_tab = getattr(session_manager, "active_tab", None)
    active_url = str(
        getattr(getattr(active_tab, "target", None), "url", "") or ""
    ).strip()
    owned_tab_platform_key = platform_key_for_url(active_url) or ""
    same_platform_tab_count = 0
    same_platform_extra_tab_count = 0
    same_platform_extra_target_ids: tuple[str, ...] = ()
    supports_same_browser_multi_tab = False
    diagnostic_reader = getattr(session_manager, "owned_tab_diagnostic", None)
    if owned_tab_platform_key and callable(diagnostic_reader):
        try:
            diagnostic = diagnostic_reader(owned_tab_platform_key)
            same_platform_tab_count = int(
                getattr(diagnostic, "same_platform_tab_count", 0) or 0
            )
            same_platform_extra_tab_count = int(
                getattr(diagnostic, "extra_tab_count", 0) or 0
            )
            same_platform_extra_target_ids = tuple(
                str(value or "")[:128]
                for value in tuple(getattr(diagnostic, "extra_target_ids", ()) or ())[:16]
            )
            supports_same_browser_multi_tab = bool(
                getattr(diagnostic, "supports_same_browser_multi_tab", False)
            )
        except (AttributeError, TypeError, ValueError):
            pass
    try:
        runtime_log_bytes = os.path.getsize(runtime_health._instance_log_path())
    except OSError:
        runtime_log_bytes = None
    return RuntimeDiagnostics(
        hunterx_rss_bytes=hunterx_rss,
        browser_rss_bytes=browser_rss,
        hunterx_cpu_percent=hunterx_cpu,
        tab_count=tab_count,
        hunterx_task_count=hunterx_tasks.active_count,
        browser_action_count=runtime_health.get_active_browser_action_count(),
        cdp_timeout_count=health.cdp_timeout_count,
        reconnect_count=health.reconnect_count,
        recovery_count=health.recovery_count,
        runtime_log_bytes=runtime_log_bytes,
        owned_tab_platform_key=owned_tab_platform_key,
        same_platform_tab_count=same_platform_tab_count,
        same_platform_extra_tab_count=same_platform_extra_tab_count,
        same_platform_extra_target_ids=same_platform_extra_target_ids,
        supports_same_browser_multi_tab=supports_same_browser_multi_tab,
    )
