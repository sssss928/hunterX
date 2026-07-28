from __future__ import annotations

import asyncio
import builtins
from pathlib import Path
import threading
import time

import pytest

import runtime_health


def test_runtime_log_rotates_and_strips_query_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "runtime.log"
    monkeypatch.setattr(runtime_health, "_instance_log_path", lambda: str(log_path))
    monkeypatch.setattr(runtime_health, "RUNTIME_LOG_MAX_BYTES", 512)
    monkeypatch.setattr(runtime_health, "RUNTIME_LOG_BACKUP_COUNT", 2)
    runtime_health._RUNTIME_LOG_SIZE_STATE.clear()

    for index in range(200):
        runtime_health.runtime_log(
            "[LEAK] offline diagnostic",
            current_url=(
                "https://user:password@tixcraft.com/ticket/area/event/game"
                f"?session=secret-{index}#token-{index}"
            ),
            sequence=index,
        )

    log_files = sorted(tmp_path.glob("runtime.log*"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in log_files)

    assert 1 <= len(log_files) <= 3
    assert all(path.stat().st_size < 1024 for path in log_files)
    assert "https://tixcraft.com/ticket/area/event/game" in combined
    assert "user:password" not in combined
    assert "secret-" not in combined
    assert "token-" not in combined


def test_runtime_log_caches_file_size_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "runtime.log"
    getsize_calls = 0
    real_getsize = runtime_health.os.path.getsize

    def count_getsize(path: str) -> int:
        nonlocal getsize_calls
        getsize_calls += 1
        return real_getsize(path)

    monkeypatch.setattr(runtime_health, "_instance_log_path", lambda: str(log_path))
    monkeypatch.setattr(runtime_health, "RUNTIME_LOG_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(runtime_health.os.path, "getsize", count_getsize)
    runtime_health._RUNTIME_LOG_SIZE_STATE.clear()

    for index in range(100):
        runtime_health.runtime_log("[CACHE TEST]", sequence=index)

    assert getsize_calls == 1


def test_runtime_log_serializes_threaded_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "runtime.log"
    real_rotate = runtime_health._rotate_runtime_log
    counter_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def tracked_rotate(path: str) -> bool:
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.001)
        try:
            return real_rotate(path)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(runtime_health, "_instance_log_path", lambda: str(log_path))
    monkeypatch.setattr(runtime_health, "RUNTIME_LOG_MAX_BYTES", 512)
    monkeypatch.setattr(runtime_health, "RUNTIME_LOG_BACKUP_COUNT", 2)
    monkeypatch.setattr(runtime_health, "_rotate_runtime_log", tracked_rotate)
    runtime_health._RUNTIME_LOG_SIZE_STATE.clear()

    threads = [
        threading.Thread(
            target=lambda: [
                runtime_health.runtime_log("[THREAD TEST]", sequence=index)
                for index in range(30)
            ]
        )
        for _ in range(4)
    ]
    for worker in threads:
        worker.start()
    for worker in threads:
        worker.join()

    assert maximum_active == 1
    assert 1 <= len(list(tmp_path.glob("runtime.log*"))) <= 3


def test_touch_heartbeat_can_throttle_hot_polling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    heartbeat_path = tmp_path / "heartbeat.txt"
    clock = {"now": 0.0}
    writes: list[Path] = []
    real_open = builtins.open

    def tracked_open(path, *args, **kwargs):
        writes.append(Path(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(
        runtime_health.util,
        "get_instance_state_path",
        lambda _filename: str(heartbeat_path),
    )
    monkeypatch.setattr(runtime_health.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(runtime_health, "open", tracked_open, raising=False)
    runtime_health._HEARTBEAT_LAST_WRITE.clear()

    runtime_health.touch_heartbeat(min_interval_seconds=1.0)
    clock["now"] = 0.05
    runtime_health.touch_heartbeat(min_interval_seconds=1.0)
    clock["now"] = 1.0
    runtime_health.touch_heartbeat(min_interval_seconds=1.0)

    assert writes == [heartbeat_path, heartbeat_path]


@pytest.mark.asyncio
async def test_wait_for_operation_can_suppress_only_success_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        runtime_health,
        "runtime_log",
        lambda message, *_args, **_kwargs: messages.append(message),
    )

    assert await runtime_health.wait_for_operation(
        asyncio.sleep(0, result="ok"),
        1.0,
        "HOT_SCAN",
        log_success=False,
    ) == "ok"
    assert messages == []

    assert await runtime_health.wait_for_operation(
        asyncio.sleep(1),
        0.1,
        "HOT_SCAN",
        default="timeout",
        log_success=False,
    ) == "timeout"
    assert messages == ["[HOT_SCAN] timeout"]
