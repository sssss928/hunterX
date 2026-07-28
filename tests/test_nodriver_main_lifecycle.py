from __future__ import annotations

import asyncio
import builtins
import sys
from types import SimpleNamespace

import pytest

import runtime_health
import util

_REAL_PLATFORM = sys.platform
try:
    # The production module re-wraps Windows stdio during import. Pytest owns
    # those capture streams, so import it through the platform-neutral path.
    sys.platform = "test"
    import nodriver_tixcraft
finally:
    sys.platform = _REAL_PLATFORM


class _FakeDriver:
    def __init__(self) -> None:
        self.main_tab = object()
        self.stop_calls = 0
        self.config = SimpleNamespace(port=0)

    async def stop(self) -> None:
        await asyncio.sleep(0)
        self.stop_calls += 1


class _FakeSessionManager:
    def __init__(self) -> None:
        self.driver = None
        self.stop_calls = 0
        self.stop_entered = None
        self.stop_release = None

    def attach(self, driver) -> None:
        self.driver = driver

    async def stop_browser(self) -> None:
        self.stop_calls += 1
        if self.stop_entered is not None:
            self.stop_entered.set()
        if self.stop_release is not None:
            await self.stop_release.wait()
        if self.driver is None:
            return
        await self.driver.stop()
        self.driver = None


def _config() -> dict:
    return {
        "homepage": "https://example.invalid/",
        "advanced": {
            "headless": True,
            "mcp_debug_port": 0,
            "show_timestamp": False,
        },
        "ocr_captcha": {"enable": False},
        "accounts": {"tixcraft_sid": ""},
    }


def _args() -> SimpleNamespace:
    return SimpleNamespace(instance=None, input=None, mcp_debug=False)


def _install_startup_mocks(monkeypatch, tmp_path, homepage_result):
    manager = _FakeSessionManager()
    driver = _FakeDriver()

    monkeypatch.setattr(nodriver_tixcraft, "get_config_dict", lambda _args: _config())
    monkeypatch.setattr(
        nodriver_tixcraft,
        "create_browser_session_manager",
        lambda *_args, **_kwargs: manager,
    )
    monkeypatch.setattr(
        nodriver_tixcraft,
        "get_extension_config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        nodriver_tixcraft,
        "nodriver_overwrite_prefs",
        lambda _conf: None,
    )

    async def fake_start(_conf):
        return driver

    async def fake_block_urls(tab, _config_dict):
        return tab

    async def fake_homepage(_driver, _config_dict):
        return homepage_result

    monkeypatch.setattr(nodriver_tixcraft.uc, "start", fake_start)
    monkeypatch.setattr(nodriver_tixcraft, "nodrver_block_urls", fake_block_urls)
    monkeypatch.setattr(
        nodriver_tixcraft,
        "nodriver_goto_homepage",
        fake_homepage,
    )
    monkeypatch.setattr(util, "get_app_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        util,
        "get_instance_state_path",
        lambda filename: str(tmp_path / filename),
    )
    monkeypatch.setattr(util, "get_instance_id", lambda: "lifecycle-test")
    return manager, driver


@pytest.mark.asyncio
async def test_main_closes_session_when_homepage_navigation_returns_none(
    monkeypatch,
    tmp_path,
) -> None:
    original_print = builtins.print
    manager, driver = _install_startup_mocks(monkeypatch, tmp_path, None)
    heartbeat_path = tmp_path / "heartbeat.txt"
    heartbeat_path.write_text("stale", encoding="utf-8")

    try:
        assert await nodriver_tixcraft.main(_args()) is None
        assert builtins.print is original_print
    finally:
        builtins.print = original_print

    assert manager.stop_calls == 1
    assert driver.stop_calls == 1
    assert not heartbeat_path.exists()


@pytest.mark.asyncio
async def test_main_preserves_exception_and_cleans_all_runtime_resources(
    monkeypatch,
    tmp_path,
) -> None:
    original_print = builtins.print
    tab = object()
    manager, driver = _install_startup_mocks(monkeypatch, tmp_path, tab)
    closed_states = []

    def touch_heartbeat(filename):
        (tmp_path / filename).write_text("live", encoding="utf-8")

    async def raise_from_loop(_config_dict):
        raise RuntimeError("loop failed")

    def fail_after_closing_ntp(state):
        closed_states.append(state)
        raise LookupError("cleanup failed")

    monkeypatch.setattr(runtime_health, "touch_heartbeat", touch_heartbeat)
    monkeypatch.setattr(
        nodriver_tixcraft,
        "_close_runtime_ntp_coordinator",
        fail_after_closing_ntp,
    )
    monkeypatch.setattr(
        nodriver_tixcraft,
        "check_and_handle_quit",
        raise_from_loop,
    )

    try:
        with pytest.raises(RuntimeError, match="loop failed"):
            await nodriver_tixcraft.main(_args())
        assert builtins.print is original_print
    finally:
        builtins.print = original_print

    assert manager.stop_calls == 1
    assert driver.stop_calls == 1
    assert len(closed_states) == 1
    assert not (tmp_path / "heartbeat.txt").exists()


@pytest.mark.asyncio
async def test_main_cancellation_propagates_after_cleanup(
    monkeypatch,
    tmp_path,
) -> None:
    original_print = builtins.print
    tab = object()
    manager, driver = _install_startup_mocks(monkeypatch, tmp_path, tab)
    entered_loop = asyncio.Event()
    wait_forever = asyncio.Event()
    closed_states = []

    def touch_heartbeat(filename):
        (tmp_path / filename).write_text("live", encoding="utf-8")

    async def block_in_loop(_config_dict):
        entered_loop.set()
        await wait_forever.wait()
        return False

    monkeypatch.setattr(runtime_health, "touch_heartbeat", touch_heartbeat)
    monkeypatch.setattr(
        nodriver_tixcraft,
        "_close_runtime_ntp_coordinator",
        closed_states.append,
    )
    monkeypatch.setattr(
        nodriver_tixcraft,
        "check_and_handle_quit",
        block_in_loop,
    )

    task = asyncio.create_task(nodriver_tixcraft.main(_args()))
    try:
        await entered_loop.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert builtins.print is original_print
    finally:
        if not task.done():
            task.cancel()
        builtins.print = original_print

    assert manager.stop_calls == 1
    assert driver.stop_calls == 1
    assert len(closed_states) == 1
    assert not (tmp_path / "heartbeat.txt").exists()


@pytest.mark.asyncio
async def test_main_finishes_browser_close_when_cancelled_during_finally(
    monkeypatch,
    tmp_path,
) -> None:
    original_print = builtins.print
    manager, driver = _install_startup_mocks(monkeypatch, tmp_path, None)
    manager.stop_entered = asyncio.Event()
    manager.stop_release = asyncio.Event()
    heartbeat_path = tmp_path / "heartbeat.txt"
    heartbeat_path.write_text("stale", encoding="utf-8")

    task = asyncio.create_task(nodriver_tixcraft.main(_args()))
    try:
        await manager.stop_entered.wait()
        task.cancel()
        manager.stop_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert builtins.print is original_print
    finally:
        manager.stop_release.set()
        if not task.done():
            task.cancel()
        builtins.print = original_print

    assert manager.stop_calls == 1
    assert driver.stop_calls == 1
    assert not heartbeat_path.exists()
