from __future__ import annotations

import nodriver_tixcraft
from platforms import tixcraft


class _Target:
    def __init__(self, url: str, target_id: str) -> None:
        self.url = url
        self.target_id = target_id


class _Tab:
    def __init__(self, url: str, target_id: str) -> None:
        self.target = _Target(url, target_id)


def _tab(suffix: str) -> _Tab:
    return _Tab(
        f"https://tixcraft.com/activity/detail/{suffix}",
        f"tixcraft-state-{suffix}",
    )


def test_real_tabs_never_use_populated_process_fallback() -> None:
    first = _tab("A")
    second = _tab("B")
    first_state = tixcraft._state_for_tab(first)
    second_state = tixcraft._state_for_tab(second)
    first_state.clear()
    second_state.clear()
    tixcraft._default_state.clear()
    tixcraft._default_state["owner"] = "legacy-process"

    try:
        assert tixcraft._dispatch_state_for_tab(first) is first_state
        assert tixcraft._dispatch_state_for_tab(second) is second_state

        first_state["owner"] = "first"
        second_state["owner"] = "second"
        assert first_state["owner"] == "first"
        assert second_state["owner"] == "second"
        assert tixcraft._default_state == {"owner": "legacy-process"}
    finally:
        first_state.clear()
        second_state.clear()
        tixcraft._default_state.clear()


def test_explicit_tab_overrides_another_tabs_ambient_binding() -> None:
    first = _tab("C")
    second = _tab("D")
    first_state = tixcraft._state_for_tab(first)
    second_state = tixcraft._state_for_tab(second)
    first_state.clear()
    second_state.clear()

    token = tixcraft._state.bind(first_state)
    try:
        assert tixcraft._state.current() is first_state
        assert tixcraft._dispatch_state_for_tab(second) is second_state
    finally:
        tixcraft._state.reset_binding(token)
        first_state.clear()
        second_state.clear()


def test_refresh_bridge_resolves_explicit_tab_without_ambient_binding() -> None:
    first = _tab("E")
    second = _tab("F")
    first_state = tixcraft._state_for_tab(first)
    second_state = tixcraft._state_for_tab(second)
    first_state.clear()
    second_state.clear()
    tixcraft._default_state.clear()
    tixcraft._default_state["owner"] = "legacy-process"

    try:
        assert tixcraft._state.has_context_binding() is False
        assert nodriver_tixcraft._get_trigger_runtime_state(
            first.target.url,
            first,
        ) is first_state
        assert nodriver_tixcraft._get_trigger_runtime_state(
            second.target.url,
            second,
        ) is second_state
    finally:
        first_state.clear()
        second_state.clear()
        tixcraft._default_state.clear()


def test_no_tab_legacy_fallback_is_diagnosed_once(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []

    def runtime_log(event, _config, **fields):
        events.append((event, fields))

    monkeypatch.setattr(tixcraft.runtime_health, "runtime_log", runtime_log)
    monkeypatch.setattr(tixcraft, "_process_fallback_diagnostic_emitted", False)
    tixcraft._default_state.clear()

    assert tixcraft._dispatch_state_for_tab(None) is tixcraft._default_state
    assert tixcraft._dispatch_state_for_tab(None) is tixcraft._default_state
    assert tixcraft._state.current() is tixcraft._default_state
    assert events == [
        (
            "[TIXCRAFT] legacy_process_state_fallback",
            {"scope": "tab_none"},
        )
    ]
