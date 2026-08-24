from __future__ import annotations

from types import SimpleNamespace

import pytest

import nodriver_tixcraft
import runtime_diagnostics
from browser_session import BrowserBootstrapResult, BrowserSessionManager
from runtime_health import RecoveryLevel, RuntimeHealthSupervisor


class _Target:
    def __init__(self, url: str, target_id: str = "") -> None:
        self.url = url
        self.target_id = target_id


class _Tab:
    def __init__(self, url: str, target_id: str = "") -> None:
        self.target = _Target(url, target_id)


class _Driver:
    def __init__(self, tab: _Tab, *, returncode: int | None = None) -> None:
        self.main_tab = tab
        self.tabs = [tab]
        self.process = SimpleNamespace(
            pid=4321,
            returncode=returncode,
            poll=lambda: returncode,
        )
        self.direct_get_calls: list[str] = []

    async def get(self, target: str):
        self.direct_get_calls.append(target)
        raise AssertionError("manager must not perform a second target navigation")


class _Manager:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.driver = None
        self.active_tab = None

    def attach(self, driver, tab=None) -> None:
        self.events.append("attach")
        self.driver = driver
        if tab is not None:
            self.active_tab = tab


def _config(homepage: str = "https://ticketplus.com.tw/activity/INITIAL") -> dict:
    return {
        "homepage": homepage,
        "advanced": {
            "headless": False,
            "window_size": "1200,800",
        },
        "accounts": {
            "ticketplus_account": "account@example.test",
        },
    }


def _install_bootstrap_spies(monkeypatch, events: list[str], drivers: list[_Driver]) -> None:
    def config_builder(config, _args, *, session_manager):
        assert session_manager is not None
        events.append(f"config:{config['homepage']}")
        return object()

    def prefs(_conf) -> None:
        events.append("prefs")

    async def start(_conf):
        events.append("start")
        return drivers.pop(0)

    async def block(tab, _config):
        events.append("block")
        return tab

    async def homepage(driver, config):
        events.append(f"homepage:{config['homepage']}")
        if config["homepage"].startswith("https://ticketplus.com.tw/activity/"):
            # TicketPlus account bootstrap intentionally lands on HOME first;
            # its captured intent returns to this activity after login.
            driver.main_tab.target.url = "https://ticketplus.com.tw/"
        else:
            driver.main_tab.target.url = config["homepage"]
        return driver.main_tab

    async def resize(_tab, _config):
        events.append("resize")

    monkeypatch.setattr(nodriver_tixcraft, "get_extension_config", config_builder)
    monkeypatch.setattr(nodriver_tixcraft, "nodriver_overwrite_prefs", prefs)
    monkeypatch.setattr(nodriver_tixcraft.uc, "start", start)
    monkeypatch.setattr(nodriver_tixcraft, "nodrver_block_urls", block)
    monkeypatch.setattr(nodriver_tixcraft, "nodriver_goto_homepage", homepage)
    monkeypatch.setattr(nodriver_tixcraft, "nodriver_resize_window", resize)


@pytest.mark.asyncio
async def test_initial_and_safe_restart_share_full_authoritative_bootstrap_order(
    monkeypatch,
) -> None:
    initial_url = "https://ticketplus.com.tw/activity/INITIAL"
    restore_url = "https://ticketplus.com.tw/activity/RESTORE"
    first_tab = _Tab("about:blank", "first")
    second_tab = _Tab("about:blank", "second")
    events: list[str] = []
    manager = _Manager(events)
    _install_bootstrap_spies(
        monkeypatch,
        events,
        [_Driver(first_tab), _Driver(second_tab)],
    )

    initial = await nodriver_tixcraft.bootstrap_owned_browser(
        _config(initial_url),
        object(),
        manager,
    )
    initial_events = list(events)
    events.clear()
    restarted = await nodriver_tixcraft.bootstrap_owned_browser(
        _config(initial_url),
        object(),
        manager,
        restore_target_url=restore_url,
        restore_platform_key="ticketplus",
    )

    assert isinstance(initial, BrowserBootstrapResult)
    assert isinstance(restarted, BrowserBootstrapResult)
    assert initial_events == [
        f"config:{initial_url}",
        "prefs",
        "start",
        "block",
        f"homepage:{initial_url}",
        "attach",
        "resize",
    ]
    assert events == [
        f"config:{restore_url}",
        "prefs",
        "start",
        "block",
        f"homepage:{restore_url}",
        "attach",
        "resize",
    ]


@pytest.mark.asyncio
async def test_ticketplus_restart_preserves_target_intent_while_login_is_required(
    monkeypatch,
) -> None:
    target_url = "https://ticketplus.com.tw/activity/TARGET"
    tab = _Tab("about:blank", "ticketplus-restart")
    events: list[str] = []
    manager = _Manager(events)
    driver = _Driver(tab)
    _install_bootstrap_spies(monkeypatch, events, [driver])

    result = await nodriver_tixcraft.bootstrap_owned_browser(
        _config(),
        object(),
        manager,
        restore_target_url=target_url,
        restore_platform_key="ticketplus",
    )

    context = nodriver_tixcraft.platform_engine.target_context_for(tab, "ticketplus")
    assert isinstance(result, BrowserBootstrapResult)
    assert context is not None
    assert context.intent.target_url == target_url
    assert context.restored_at is None
    assert tab.target.url == "https://ticketplus.com.tw/"
    assert driver.direct_get_calls == []


def test_pending_restart_target_survives_outer_dispatch_without_mutating_config() -> None:
    configured_url = "https://ticketplus.com.tw/activity/CONFIGURED"
    restore_url = "https://ticketplus.com.tw/activity/RESTORE"
    tab = _Tab("https://ticketplus.com.tw/", "pending-restore")
    config = _config(configured_url)
    context = nodriver_tixcraft.platform_engine.capture_navigation_intent(
        tab,
        "ticketplus",
        restore_url,
        config,
        reason="test_safe_restart",
    )

    dispatch_config = nodriver_tixcraft._config_with_pending_safe_restore(
        config,
        target_url=restore_url,
        platform_key="ticketplus",
        current_url=tab.target.url,
    )
    nodriver_tixcraft.platform_engine.before_dispatch(
        tab,
        tab.target.url,
        dispatch_config,
    )

    assert config["homepage"] == configured_url
    assert dispatch_config["homepage"] == restore_url
    assert nodriver_tixcraft.platform_engine.target_context_for(tab, "ticketplus") is context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_url", "platform_key"),
    [
        ("https://tixcraft.com/ticket/area/EVENT/1", "tixcraft"),
        ("https://ticket.ibon.com.tw/ActivityInfo/Details/EVENT", "ibon"),
        ("https://kktix.com/events/EVENT", "kktix"),
    ],
)
async def test_restart_replays_platform_homepage_prerequisites_once(
    monkeypatch,
    target_url: str,
    platform_key: str,
) -> None:
    tab = _Tab("about:blank", platform_key)
    events: list[str] = []
    manager = _Manager(events)
    _install_bootstrap_spies(monkeypatch, events, [_Driver(tab)])

    result = await nodriver_tixcraft.bootstrap_owned_browser(
        _config(),
        object(),
        manager,
        restore_target_url=target_url,
        restore_platform_key=platform_key,
    )

    assert isinstance(result, BrowserBootstrapResult)
    assert events.count("start") == 1
    assert events.count("block") == 1
    assert events.count(f"homepage:{target_url}") == 1
    assert events.count("attach") == 1
    assert events.count("resize") == 1


@pytest.mark.asyncio
async def test_bootstrap_keeps_homepage_tab_when_driver_has_no_initial_tab(
    monkeypatch,
) -> None:
    tab = _Tab("https://kktix.com/events/EVENT", "created-homepage")
    driver = SimpleNamespace(main_tab=None, tabs=[])
    manager = _Manager([])
    block_calls: list[_Tab] = []
    monkeypatch.setattr(
        nodriver_tixcraft,
        "get_extension_config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(nodriver_tixcraft, "nodriver_overwrite_prefs", lambda _conf: None)

    async def start(_conf):
        return driver

    async def homepage(_driver, _config):
        return tab

    async def block_without_return(value, _config):
        block_calls.append(value)

    monkeypatch.setattr(nodriver_tixcraft.uc, "start", start)
    monkeypatch.setattr(nodriver_tixcraft, "nodriver_goto_homepage", homepage)
    monkeypatch.setattr(nodriver_tixcraft, "nodrver_block_urls", block_without_return)
    monkeypatch.setattr(
        nodriver_tixcraft,
        "nodriver_resize_window",
        lambda *_args, **_kwargs: None,
    )

    result = await nodriver_tixcraft.bootstrap_owned_browser(
        {"homepage": tab.target.url, "advanced": {"headless": True}},
        object(),
        manager,
    )

    assert isinstance(result, BrowserBootstrapResult)
    assert result.tab is tab
    assert manager.active_tab is tab
    assert block_calls == [tab]


@pytest.mark.asyncio
async def test_bootstrap_rejects_protected_or_cross_platform_target_before_start(
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        nodriver_tixcraft,
        "get_extension_config",
        lambda *_args, **_kwargs: calls.append("config"),
    )

    with pytest.raises(ValueError, match="safe restart target"):
        await nodriver_tixcraft.bootstrap_owned_browser(
            _config(),
            object(),
            _Manager(calls),
            restore_target_url="https://tixcraft.com/ticket/ticket/EVENT/1",
            restore_platform_key="tixcraft",
        )
    with pytest.raises(ValueError, match="safe restart target"):
        await nodriver_tixcraft.bootstrap_owned_browser(
            _config(),
            object(),
            _Manager(calls),
            restore_target_url="https://ticketplus.com.tw/activity/TARGET",
            restore_platform_key="kktix",
        )

    assert calls == []


@pytest.mark.asyncio
async def test_manager_fails_closed_on_driver_only_legacy_restart_result() -> None:
    old = _Driver(_Tab("", "old"), returncode=1)
    replacement = _Driver(_Tab("https://ticketplus.com.tw/", "new"))
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(old, old.main_tab)
    manager.set_restart_factory(lambda: replacement)

    result = await manager.recover(
        RecoveryLevel.SAFE_RESTART,
        target_url="https://ticketplus.com.tw/activity/TARGET",
        platform_key="ticketplus",
        allow_restart=True,
    )

    assert result.success is False
    assert result.reason == "browser_bootstrap_incomplete"
    assert result.restarted is True
    assert manager.driver is replacement
    assert manager.active_tab is None
    assert replacement.direct_get_calls == []


@pytest.mark.parametrize(
    ("platform_key", "urls"),
    [
        (
            "ticketplus",
            [f"https://ticketplus.com.tw/activity/{value}" for value in "ABC"],
        ),
        (
            "tixcraft",
            [f"https://tixcraft.com/activity/detail/{value}" for value in "ABC"],
        ),
        (
            "kktix",
            [f"https://kktix.com/events/{value}" for value in "ABC"],
        ),
    ],
)
def test_owned_tab_diagnostic_never_automates_three_same_platform_tabs(
    platform_key: str,
    urls: list[str],
) -> None:
    tabs = [_Tab(url, f"target-{index}") for index, url in enumerate(urls)]
    driver = SimpleNamespace(tabs=list(tabs), main_tab=tabs[0])
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(driver, tabs[0])

    diagnostic = manager.owned_tab_diagnostic(platform_key)

    assert diagnostic.supports_same_browser_multi_tab is False
    assert diagnostic.automated_tab is tabs[0]
    assert diagnostic.same_platform_tab_count == 3
    assert diagnostic.extra_tab_count == 2
    assert diagnostic.extra_tabs == tuple(tabs[1:])
    assert manager.active_tab is tabs[0]
    assert driver.tabs == tabs


def test_owned_tab_diagnostic_mixed_tabs_is_platform_scoped_and_bounded() -> None:
    owned = _Tab("https://ticketplus.com.tw/activity/OWNED", "owned")
    ticketplus_extras = [
        _Tab(f"https://ticketplus.com.tw/activity/{index}", f"tp-{index}")
        for index in range(20)
    ]
    foreign = [
        _Tab("https://tixcraft.com/activity/detail/T", "tixcraft"),
        _Tab("https://kktix.com/events/K", "kktix"),
    ]
    tabs = [owned, *ticketplus_extras, *foreign]
    driver = SimpleNamespace(tabs=list(tabs), main_tab=owned)
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(driver, owned)

    diagnostic = manager.owned_tab_diagnostic("ticketplus")

    assert diagnostic.same_platform_tab_count == 21
    assert diagnostic.extra_tab_count == 20
    assert diagnostic.extra_tabs == tuple(ticketplus_extras[:16])
    assert len(diagnostic.extra_target_ids) == 16
    assert diagnostic.truncated is True
    assert manager.active_tab is owned
    assert driver.tabs == tabs


def test_low_frequency_runtime_fields_report_extras_without_claiming_ownership(
    monkeypatch,
    tmp_path,
) -> None:
    owned = _Tab("https://kktix.com/events/OWNED", "owned")
    extras = [
        _Tab("https://kktix.com/events/EXTRA-1", "extra-1"),
        _Tab("https://kktix.com/events/EXTRA-2", "extra-2"),
    ]
    driver = SimpleNamespace(tabs=[owned, *extras], main_tab=owned, process=None)
    manager = BrowserSessionManager({"advanced": {}})
    manager.attach(driver, owned)
    monkeypatch.setattr(runtime_diagnostics, "_process_metrics", lambda _pid: (0, 0.0))
    monkeypatch.setattr(runtime_diagnostics, "_process_tree_rss_bytes", lambda _pid: 0)
    monkeypatch.setattr(
        runtime_diagnostics.runtime_health,
        "_instance_log_path",
        lambda: str(tmp_path / "missing.log"),
    )

    snapshot = runtime_diagnostics.collect_runtime_diagnostics(
        manager,
        RuntimeHealthSupervisor(),
    )

    assert snapshot.owned_tab_platform_key == "kktix"
    assert snapshot.same_platform_tab_count == 3
    assert snapshot.same_platform_extra_tab_count == 2
    assert snapshot.same_platform_extra_target_ids == ("extra-1", "extra-2")
    assert snapshot.supports_same_browser_multi_tab is False
    assert manager.active_tab is owned
