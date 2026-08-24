from __future__ import annotations

import asyncio

import pytest

from browser_session import BrowserSessionManager
from runtime_health import RecoveryLevel


class _Target:
    def __init__(self, url: str, target_id: str = "") -> None:
        self.url = url
        self.target_id = target_id


class _Tab:
    def __init__(
        self,
        url: str,
        target_id: str = "",
        *,
        live_url: str | None = None,
        live_target_id: str | None = None,
        proof_error: Exception | None = None,
        proof_delay: float = 0.0,
    ) -> None:
        self.target = _Target(url, target_id)
        self.live_url = url if live_url is None else live_url
        self.live_target_id = target_id if live_target_id is None else live_target_id
        self.proof_error = proof_error
        self.proof_delay = proof_delay
        self.proof_calls = 0

    async def send(self, _command):
        self.proof_calls += 1
        if self.proof_delay:
            await asyncio.sleep(self.proof_delay)
        if self.proof_error is not None:
            raise self.proof_error
        return _Target(self.live_url, self.live_target_id)


class _Driver:
    def __init__(self, tabs: list[_Tab], update_error: Exception | None = None) -> None:
        self.tabs = tabs
        self.main_tab = tabs[0] if tabs else None
        self.update_calls = 0
        self.update_error = update_error

    async def update_targets(self) -> None:
        self.update_calls += 1
        if self.update_error is not None:
            raise self.update_error


def _manager(tabs: list[_Tab], active_tab: _Tab | None = None) -> BrowserSessionManager:
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(_Driver(tabs), active_tab)
    return manager


def test_reacquire_rejects_empty_target_and_sole_wrong_event() -> None:
    wrong = _Tab("https://ticketplus.com.tw/activity/OTHER", "wrong")
    manager = _manager([wrong])

    assert manager.reacquire_tab("", "ticketplus") is None
    assert manager.reacquire_tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "ticketplus",
    ) is None
    assert manager.active_tab is None


def test_reacquire_accepts_unique_canonical_exact_among_wrong_tabs() -> None:
    wrong = _Tab("https://ticketplus.com.tw/activity/OTHER", "wrong")
    exact = _Tab(
        "https://ticketplus.com.tw/activity/TARGET?utm_medium=ignored",
        "exact",
    )
    manager = _manager([wrong, exact])

    result = manager.reacquire_tab(
        "https://ticketplus.com.tw/activity/TARGET/?utm_source=ignored",
        "ticketplus",
    )

    assert result is exact
    assert manager.active_tab is exact


def test_duplicate_exact_is_ambiguous_without_owned_target_id() -> None:
    first = _Tab("https://ticketplus.com.tw/activity/TARGET", "first")
    second = _Tab("https://ticketplus.com.tw/activity/TARGET/", "second")
    manager = _manager([first, second])

    assert manager.reacquire_tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "ticketplus",
    ) is None
    assert manager.active_tab is None


def test_duplicate_exact_uses_saved_owned_target_id_to_disambiguate() -> None:
    previous = _Tab("https://ticketplus.com.tw/activity/TARGET", "owned")
    wrong_duplicate = _Tab("https://ticketplus.com.tw/activity/TARGET", "other")
    owned_replacement = _Tab("https://ticketplus.com.tw/activity/TARGET/", "owned")
    manager = _manager([wrong_duplicate, owned_replacement], previous)

    result = manager.reacquire_tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "ticketplus",
    )

    assert result is owned_replacement
    assert manager.active_tab is owned_replacement


def test_saved_owned_target_id_allows_same_platform_route_advance() -> None:
    previous = _Tab("https://ticketplus.com.tw/activity/TARGET", "owned")
    advanced = _Tab("https://ticketplus.com.tw/order/TARGET/session", "owned")
    manager = _manager([advanced], previous)

    result = manager.reacquire_tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "ticketplus",
    )

    assert result is advanced
    assert manager.active_tab is advanced


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [RecoveryLevel.REACQUIRE, RecoveryLevel.TRANSPORT_REBIND])
async def test_recovery_requires_active_read_only_target_proof(level: RecoveryLevel) -> None:
    previous = _Tab("https://ticketplus.com.tw/activity/TARGET", "owned")
    dead = _Tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "owned",
        proof_error=ConnectionError("WebSocket connection closed"),
    )
    manager = _manager([dead], previous)

    result = await manager.recover(
        level,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is False
    assert result.reason == "transport_probe_failed"
    assert result.tab is previous
    assert manager.active_tab is previous
    assert dead.proof_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [RecoveryLevel.REACQUIRE, RecoveryLevel.TRANSPORT_REBIND])
async def test_recovery_commits_candidate_only_after_live_proof(level: RecoveryLevel) -> None:
    previous = _Tab("https://ticketplus.com.tw/activity/TARGET", "owned")
    replacement = _Tab("https://ticketplus.com.tw/activity/TARGET", "owned")
    manager = _manager([replacement], previous)

    result = await manager.recover(
        level,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is True
    assert result.tab is replacement
    assert manager.active_tab is replacement
    assert replacement.proof_calls == 1


@pytest.mark.asyncio
async def test_live_proof_rejects_stale_cached_url_for_wrong_live_event() -> None:
    stale = _Tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "candidate",
        live_url="https://ticketplus.com.tw/activity/OTHER",
    )
    manager = _manager([stale])

    result = await manager.recover(
        RecoveryLevel.TRANSPORT_REBIND,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is False
    assert result.reason == "transport_probe_failed"
    assert manager.active_tab is None


@pytest.mark.asyncio
async def test_live_proof_rejects_target_id_mismatch() -> None:
    candidate = _Tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "cached-id",
        live_target_id="different-live-id",
    )
    manager = _manager([candidate])

    result = await manager.recover(
        RecoveryLevel.REACQUIRE,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is False
    assert result.reason == "transport_probe_failed"
    assert manager.active_tab is None


@pytest.mark.asyncio
async def test_live_proof_is_bounded_and_keeps_previous_owner_on_timeout() -> None:
    previous = _Tab("https://ticketplus.com.tw/activity/TARGET", "owned")
    stalled = _Tab(
        "https://ticketplus.com.tw/activity/TARGET",
        "owned",
        proof_delay=0.2,
    )
    manager = _manager([stalled], previous)
    manager.target_probe_timeout_seconds = 0.01

    result = await manager.recover(
        RecoveryLevel.TRANSPORT_REBIND,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is False
    assert result.reason == "transport_probe_failed"
    assert manager.active_tab is previous
    assert stalled.proof_calls == 1


@pytest.mark.asyncio
async def test_refresh_failure_can_recover_only_when_target_proof_succeeds() -> None:
    replacement = _Tab("https://ticketplus.com.tw/activity/TARGET", "replacement")
    driver = _Driver([replacement], RuntimeError("browser target refresh failed"))
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(driver)

    result = await manager.recover(
        RecoveryLevel.TRANSPORT_REBIND,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=False,
    )

    assert result.success is True
    assert result.tab is replacement
    assert manager.active_tab is replacement
    assert driver.update_calls == 1
    assert replacement.proof_calls == 1
