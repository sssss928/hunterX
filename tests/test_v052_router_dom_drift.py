from __future__ import annotations

from pathlib import Path

import pytest

from dom_drift import SelectorCandidate, resolve_selector
from page_classifier import PageClass
from platform_engine import PlatformEngine


class _Tab:
    def __init__(self, target_id: str = "target-1") -> None:
        self.target = type("Target", (), {"target_id": target_id})()


def _route(path: str) -> str:
    return f"https://ticketplus.com.tw{path}"


def test_push_replace_pop_and_same_url_rerender_are_generation_bounded() -> None:
    engine = PlatformEngine()
    tab = _Tab()

    initial = engine.before_dispatch(
        tab,
        _route("/activity/demo/session"),
        {},
        dom_signature="generation-1",
    )
    assert initial.route_generation == 1
    assert initial.route_transition_reasons == ("initial",)

    pushed = engine.before_dispatch(
        tab,
        _route("/activity/demo/session?view=area"),
        {},
        dom_signature="generation-1",
    )
    assert pushed.route_generation == 2
    assert "url_changed" in pushed.route_transition_reasons

    replaced = engine.before_dispatch(
        tab,
        _route("/activity/demo/session?view=ticket"),
        {},
        dom_signature="generation-1",
    )
    assert replaced.route_generation == 3

    popped = engine.before_dispatch(
        tab,
        _route("/activity/demo/session"),
        {},
        dom_signature="generation-1",
    )
    assert popped.route_generation == 4

    rerendered = engine.before_dispatch(
        tab,
        _route("/activity/demo/session"),
        {},
        dom_signature="generation-2",
    )
    assert rerendered.route_generation == 5
    assert rerendered.route_transition_reasons == ("same_url_dom_rerender",)

    stable = engine.before_dispatch(
        tab,
        _route("/activity/demo/session"),
        {},
        dom_signature="generation-2",
    )
    assert stable.route_generation == 5
    assert stable.route_transition_reasons == ()


def test_tracking_only_replace_state_does_not_restart_route_or_attempt() -> None:
    engine = PlatformEngine()
    tab = _Tab()
    first = engine.before_dispatch(tab, _route("/activity/demo/session"), {})
    tracked = engine.before_dispatch(
        tab,
        _route("/activity/demo/session?utm_source=synthetic&fbclid=1"),
        {},
    )
    assert tracked.route_generation == first.route_generation
    assert tracked.attempt_id == first.attempt_id
    assert tracked.new_attempt_started is False


def test_target_replacement_is_detected_once_without_listener_installation() -> None:
    engine = PlatformEngine()
    tab = _Tab("old")
    first = engine.before_dispatch(tab, _route("/activity/demo/session"), {})
    tab.target.target_id = "replacement"
    replaced = engine.before_dispatch(
        tab,
        _route("/activity/demo/session"),
        {},
        dom_signature="target-check",
    )
    stable = engine.before_dispatch(tab, _route("/activity/demo/session"), {})
    assert replaced.route_generation == first.route_generation + 1
    assert replaced.route_transition_reasons == ("target_replaced",)
    assert stable.route_generation == replaced.route_generation
    assert stable.route_transition_reasons == ()


class _SelectorTab:
    def __init__(self, available: dict[str, object]) -> None:
        self.available = available
        self.queries: list[str] = []

    async def query_selector(self, selector: str):
        self.queries.append(selector)
        return self.available.get(selector)


def _candidate(selector: str, confidence: float, evidence: str = "aria/test fixture"):
    return SelectorCandidate(
        selector,
        confidence,
        evidence,
        frozenset({PageClass.AREA}),
    )


@pytest.mark.asyncio
async def test_primary_missing_uses_only_known_high_confidence_fallback(monkeypatch) -> None:
    fallback_element = object()
    tab = _SelectorTab({"[data-testid=known-area]": fallback_element})
    logs: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "dom_drift.runtime_health.runtime_log",
        lambda message, _config=None, **fields: logs.append((message, fields)),
    )

    result = await resolve_selector(
        tab,
        _candidate("#primary-area", 1.0),
        (
            _candidate(".guess", 0.4, "weak text guess"),
            _candidate("[data-testid=known-area]", 0.95),
        ),
        page_class=PageClass.AREA,
    )

    assert result.element is fallback_element
    assert result.used_fallback is True
    assert result.selector == "[data-testid=known-area]"
    assert tab.queries == ["#primary-area", "[data-testid=known-area]"]
    assert logs and logs[-1][0] == "DOM_DRIFT"


@pytest.mark.asyncio
async def test_unknown_dom_fails_closed_without_click_or_low_confidence_query(monkeypatch) -> None:
    tab = _SelectorTab({".guess": object()})
    monkeypatch.setattr("dom_drift.runtime_health.runtime_log", lambda *_a, **_k: None)
    result = await resolve_selector(
        tab,
        _candidate("#primary-area", 1.0),
        (_candidate(".guess", 0.2, "weak"),),
        page_class=PageClass.AREA,
    )
    assert result.element is None
    assert result.reason == "no_confident_selector"
    assert tab.queries == ["#primary-area"]


def test_synthetic_router_has_single_idempotent_install_and_no_body_observer() -> None:
    source = (
        Path(__file__).resolve().parent / "fixtures" / "synthetic_ticket_spa.html"
    ).read_text(encoding="utf-8")
    assert "__hunterxSyntheticInstalled" in source
    assert source.count("window.__hunterxSyntheticInstalled = true") == 1
    assert "MutationObserver" not in source
    assert "document.body" not in source
