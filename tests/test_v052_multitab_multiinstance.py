from __future__ import annotations

import pytest

from browser_session import BrowserSessionManager
from platform_adapters import adapter_for_key
from platform_contract import PlatformStateProxy, clear_active_platform_state
from platform_engine import PlatformEngine


@pytest.fixture(autouse=True)
def _isolate_platform_state_binding():
    """Do not leak a synthetic dispatch binding into the next test."""

    clear_active_platform_state()
    try:
        yield
    finally:
        clear_active_platform_state()


class _Tab:
    def __init__(self, identity: str) -> None:
        self.target = type("Target", (), {"target_id": identity})()


ROUTES = (
    ("ticketplus", "https://ticketplus.com.tw/activity/event-a/session-a"),
    ("tixcraft", "https://tixcraft.com/ticket/area/event-b/1"),
    ("kktix", "https://demo.kktix.cc/events/event-c/registrations/new"),
)


def _config(mode: str, homepage: str) -> dict:
    return {
        "homepage": homepage,
        "advanced": {
            "run_mode": mode,
            "auto_reload_page_interval": 0,
            "leak_refresh_interval_seconds": 3,
        },
    }


def test_three_tabs_x_1000_transitions_keep_attempt_and_platform_state_isolated() -> None:
    engine = PlatformEngine()
    tabs = [_Tab(f"tab-{index}") for index in range(3)]
    expected: dict[tuple[int, str], int] = {}

    for iteration in range(1000):
        for tab_index, (tab, (platform_key, route)) in enumerate(zip(tabs, ROUTES)):
            mode = "leak_watch" if (iteration + tab_index) % 2 else "onsale"
            event_route = f"{route}?iteration={iteration}&tab={tab_index}"
            decision = engine.before_dispatch(
                tab,
                event_route,
                _config(mode, event_route),
                now=float(iteration),
            )
            adapter = adapter_for_key(platform_key)
            assert adapter is not None
            state = engine.state_for(tab, adapter)
            state.platform_data["sentinel"] = (tab_index, iteration, mode)
            expected[(tab_index, platform_key)] = iteration
            assert decision.platform_key == platform_key
            assert decision.attempt_id == state.attempt.attempt_id
            assert state.attempt.tab_identity == f"target:tab-{tab_index}"

        for tab_index, (tab, (platform_key, _route)) in enumerate(zip(tabs, ROUTES)):
            adapter = adapter_for_key(platform_key)
            assert adapter is not None
            state = engine.state_for(tab, adapter)
            expected_iteration = expected[(tab_index, platform_key)]
            assert state.platform_data["sentinel"][:2] == (
                tab_index,
                expected_iteration,
            )

    assert engine.state_count == 3
    assert engine.refresh_coordinator_count == 3
    assert len({engine.current_attempt(tab, key).attempt_id for tab, (key, _) in zip(tabs, ROUTES)}) == 3


def test_three_instances_x_1000_transitions_have_independent_generations() -> None:
    engines = [PlatformEngine() for _ in range(3)]
    tabs = [_Tab(f"instance-{index}-tab") for index in range(3)]
    adapter = adapter_for_key("ticketplus")
    assert adapter is not None
    safe = "https://ticketplus.com.tw/activity/demo/session"
    protected = "https://ticketplus.com.tw/confirm/demo/session"

    for generation in range(1, 1001):
        for instance_index, (engine, tab) in enumerate(zip(engines, tabs)):
            decision = engine.before_dispatch(
                tab,
                safe,
                _config("leak_watch" if instance_index == 1 else "onsale", safe),
            )
            attempt = engine.current_attempt(tab, adapter)
            assert attempt is not None
            assert attempt.generation == generation
            assert decision.automation_allowed is True
            assert engine.claim_submit(tab, adapter, owner=f"instance-{instance_index}")
            engine.before_dispatch(tab, protected, {})
            engine.mark_attempt_completed(tab, adapter, reason="synthetic_success")

    attempts = [engine.current_attempt(tab, adapter) for engine, tab in zip(engines, tabs)]
    assert all(attempt is not None and attempt.generation == 1000 for attempt in attempts)
    assert len({attempt.attempt_id for attempt in attempts if attempt is not None}) == 3
    assert all(engine.state_count == 1 for engine in engines)


def test_context_bound_platform_proxy_does_not_cross_contaminate_tabs() -> None:
    engine = PlatformEngine()
    adapter = adapter_for_key("ticketplus")
    assert adapter is not None
    proxy = PlatformStateProxy("ticketplus")
    tab_a = _Tab("a")
    tab_b = _Tab("b")
    route = "https://ticketplus.com.tw/activity/demo/session"

    engine.before_dispatch(tab_a, route, {})
    token_a = proxy.bind(engine.state_for(tab_a, adapter).platform_data)
    try:
        proxy["owner"] = "a"
    finally:
        proxy.reset_binding(token_a)

    engine.before_dispatch(tab_b, route, {})
    token_b = proxy.bind(engine.state_for(tab_b, adapter).platform_data)
    try:
        proxy["owner"] = "b"
    finally:
        proxy.reset_binding(token_b)

    assert engine.state_for(tab_a, adapter).platform_data == {"owner": "a"}
    assert engine.state_for(tab_b, adapter).platform_data == {"owner": "b"}


def test_instance_profile_and_private_mode_ownership_are_isolated(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("browser_session.util.get_app_root", lambda: str(tmp_path))
    managers = []
    for instance in ("one", "two", "three"):
        args = type(
            "Args",
            (),
            {"instance": instance, "browser": "chrome", "browser_private_mode": False},
        )()
        managers.append(BrowserSessionManager({"advanced": {}}, args))

    paths = {manager.profile_dir() for manager in managers}
    assert len(paths) == 3
    assert {path.name for path in paths} == {"one", "two", "three"}

