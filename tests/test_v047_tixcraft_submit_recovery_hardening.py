from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from page_classifier import PageClass
from platforms import tixcraft


TICKET_URL = "https://tixcraft.com/ticket/ticket/26_event/5300/1/2"
AREA_URL = "https://tixcraft.com/ticket/area/26_event/5300"


class _Tab:
    def __init__(self, url: str = TICKET_URL) -> None:
        self.target = SimpleNamespace(url=url)
        self.handlers: list[tuple[object, object]] = []

    async def send(self, _command):
        return None

    def add_handler(self, event, callback) -> None:
        self.handlers.append((event, callback))


def _config() -> dict:
    return {
        "homepage": "https://tixcraft.com/activity/detail/26_event",
        "ticket_number": 2,
        "tixcraft": {"allow_less_tickets": False},
        "ocr_captcha": {"force_submit": True},
        "advanced": {
            "headless": False,
            "play_sound": {"ticket": False, "order": False},
        },
        "area_auto_select": {"enable": True},
        "date_auto_select": {"enable": True},
    }


class _CaptchaInput:
    def __init__(self) -> None:
        self.clicked = 0
        self.keys: list[str] = []

    async def apply(self, script):
        if "return element.value" in script:
            return ""
        return None

    async def click(self):
        self.clicked += 1

    async def send_keys(self, value):
        self.keys.append(value)


class _ManualCaptchaTab(_Tab):
    def __init__(self) -> None:
        super().__init__()
        self.input = _CaptchaInput()

    async def query_selector(self, selector):
        if selector == "#TicketForm_verifyCode":
            return self.input
        return None

    async def evaluate(self, script):
        if "offsetParent" in script:
            return True
        return None


class _AreaLink:
    def __init__(self) -> None:
        self.clicks = 0

    async def click(self):
        self.clicks += 1


class _AreaZone:
    def __init__(self, link) -> None:
        self.link = link

    async def query_selector_all(self, _selector):
        return [self.link]


class _AreaTab(_Tab):
    def __init__(self) -> None:
        super().__init__(AREA_URL)
        self.link = _AreaLink()
        self.zone = _AreaZone(self.link)

    async def query_selector(self, selector):
        return self.zone if selector == ".zone" else None

    async def evaluate(self, script):
        if "document.querySelectorAll('.zone a')" in script:
            return json.dumps([{"text": "VIP A區", "fontText": "20"}])
        return None


def _seed_submitted_ticket_state(tab: _Tab, *, elapsed: float = 3.0) -> str:
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()
    tixcraft._state.update(
        {
            "current_event_id": "26_event",
            "current_game_id": "5300",
            "current_event_origin": "https://tixcraft.com",
            "last_valid_area_url": AREA_URL,
            "last_selected_area": "VIP",
            "notification_flow_generation": 7,
        }
    )
    tixcraft._begin_tixcraft_purchase_attempt(
        "ticket_page",
        TICKET_URL,
        seat_area="VIP",
    )
    tixcraft._mark_tixcraft_submit_started(TICKET_URL)
    tixcraft._state["notification_submit_started_at"] = time.monotonic() - elapsed
    tixcraft._state["captcha_submit_until"] = time.time() - 0.01
    key = "ticket_assigned_{}_2_0".format(TICKET_URL)
    tixcraft._state[key] = True
    return key


@pytest.mark.asyncio
async def test_submit_context_is_armed_before_ambiguous_keydown_failure() -> None:
    class _ReplacingDocumentTab(_Tab):
        async def send(self, _command):
            raise RuntimeError("document was replaced while dispatching keyDown")

    tab = _ReplacingDocumentTab()
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()
    tixcraft._state.update(
        {
            "current_event_id": "26_event",
            "current_game_id": "5300",
            "last_valid_area_url": AREA_URL,
        }
    )
    guard = tixcraft._state["submit_guard"]

    assert await tixcraft._dispatch_tixcraft_enter_submit(tab, TICKET_URL, guard)
    assert tixcraft._has_confirmed_tixcraft_submit_context()
    attempt = tixcraft._get_tixcraft_purchase_attempt()
    assert attempt is not None
    assert attempt.phase.value == "submit_in_flight"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "ocr_enabled"),
    [("", False), ("AB12", True)],
)
async def test_manual_and_non_auto_ocr_captcha_are_protected_before_human_enter(
    answer: str,
    ocr_enabled: bool,
) -> None:
    tab = _ManualCaptchaTab()
    config = _config()
    config["ocr_captcha"].update(
        {"enable": ocr_enabled, "force_submit": False}
    )
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    editing, submitted = await tixcraft.nodriver_tixcraft_keyin_captcha_code(
        tab,
        answer=answer,
        auto_submit=False,
        config_dict=config,
    )

    assert not submitted
    assert tixcraft._is_tixcraft_submit_in_flight(tab)
    assert not tixcraft._state["submit_guard"].can_submit(TICKET_URL)
    assert editing is (answer == "")


@pytest.mark.asyncio
async def test_successful_keydown_with_document_replacement_on_keyup_stays_protected() -> None:
    class _KeyUpReplacementTab(_Tab):
        def __init__(self):
            super().__init__()
            self.events = 0

        async def send(self, _command):
            self.events += 1
            if self.events == 2:
                raise RuntimeError("document replaced before keyUp")
            return None

    tab = _KeyUpReplacementTab()
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    assert await tixcraft._dispatch_tixcraft_enter_submit(
        tab,
        TICKET_URL,
        tixcraft._state["submit_guard"],
    )
    assert tab.events == 2
    assert tixcraft._is_tixcraft_submit_in_flight(tab)


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_less", [False, True])
async def test_ticket_count_fallback_flag_is_applied_to_production_selector(
    allow_less: bool,
) -> None:
    class _TicketCountTab:
        def __init__(self):
            self.script = ""

        async def evaluate(self, script):
            self.script = script
            return {
                "success": allow_less,
                "selected": "1" if allow_less else "",
                "fallback": allow_less,
            }

    tab = _TicketCountTab()

    assigned = await tixcraft.nodriver_ticket_number_select_fill(
        tab,
        object(),
        "2",
        allow_less_tickets=allow_less,
    )

    assert assigned is allow_less
    assert f"if (!{str(allow_less).lower()})" in tab.script


def test_stale_submit_generation_token_and_tab_identity_are_rejected() -> None:
    tab = _Tab()
    other_tab = _Tab()
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()
    tixcraft._mark_tixcraft_submit_started(TICKET_URL, tab=tab)
    original_generation = tixcraft._state["notification_flow_generation"]
    original_token = tixcraft._state["submit_generation"]

    assert tixcraft._is_tixcraft_submit_in_flight(tab)
    assert not tixcraft._is_tixcraft_submit_in_flight(other_tab)

    tixcraft._state["submit_generation"] = original_token + 1
    assert not tixcraft._is_tixcraft_submit_in_flight(tab)
    tixcraft._state["submit_generation"] = original_token

    tixcraft._state["notification_flow_generation"] = original_generation + 1
    assert not tixcraft._is_tixcraft_submit_in_flight(tab)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "from top to bottom",
        "from bottom to top",
        "center",
        "random",
        "most remaining",
    ],
)
@pytest.mark.parametrize(
    ("keyword", "keyword_exclude"),
    [
        ("", ""),
        ('"VIP"', ""),
        ('"NOPE","VIP"', ""),
        ('"VIP A區"', ""),
        ('"VIP"', '"Restricted View"'),
    ],
)
async def test_all_area_modes_and_keyword_paths_share_submit_protection(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    keyword: str,
    keyword_exclude: str,
) -> None:
    tab = _AreaTab()
    config = _config()
    config.update(
        {
            "keyword_exclude": keyword_exclude,
            "area_auto_fallback": False,
        }
    )
    config["area_auto_select"].update(
        {"area_keyword": keyword, "mode": mode}
    )

    async def not_paused(_config):
        return False

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", not_paused)
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    assert await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    assert tab.link.clicks == 1
    pending = tixcraft._state["pending_area_navigation"]
    assert pending.source_url == AREA_URL

    assert tixcraft._reconcile_tixcraft_pending_navigation(
        tab,
        TICKET_URL,
        PageClass.TICKET,
        config,
    )
    tixcraft._mark_tixcraft_submit_started(TICKET_URL, tab=tab)
    assert tixcraft._is_tixcraft_submit_in_flight(tab)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keyword", "keyword_exclude"),
    [
        ('"NO MATCH"', ""),
        ("", '"VIP"'),
    ],
)
async def test_no_matching_or_excluded_area_never_arms_submit(
    monkeypatch: pytest.MonkeyPatch,
    keyword: str,
    keyword_exclude: str,
) -> None:
    tab = _AreaTab()
    config = _config()
    config.update({"keyword_exclude": keyword_exclude, "area_auto_fallback": False})
    config["area_auto_select"].update(
        {"area_keyword": keyword, "mode": "from top to bottom"}
    )
    reloads: list[str] = []

    async def not_paused(_config):
        return False

    async def record_reload(_tab, reason="", **_kwargs):
        reloads.append(reason)
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(tixcraft, "guarded_reload", record_reload)
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    assert not await tixcraft.nodriver_tixcraft_area_auto_select(tab, AREA_URL, config)
    assert tab.link.clicks == 0
    assert "pending_area_navigation" not in tixcraft._state
    assert not tixcraft._is_tixcraft_submit_in_flight(tab)
    assert reloads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [0.2, 1.5, 3.0, 10.0, 20.0])
async def test_expired_captcha_throttle_never_recovers_during_submit_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    elapsed: float,
) -> None:
    tab = _Tab()
    _seed_submitted_ticket_state(tab, elapsed=elapsed)
    navigations: list[tuple[str, str]] = []

    async def not_paused(_config):
        return False

    async def current_url(_tab):
        return TICKET_URL, None

    async def ticket_dom_inconclusive(_tab, _config):
        return False

    async def record_navigation(_tab, target, _config, reason=""):
        navigations.append((target, reason))
        return True

    async def not_ready(_tab, _config):
        return False

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(tixcraft, "nodriver_current_url", current_url)
    monkeypatch.setattr(tixcraft, "_is_tixcraft_ticket_count_ready", ticket_dom_inconclusive)
    monkeypatch.setattr(tixcraft.runtime_health, "guarded_get", record_navigation)
    monkeypatch.setattr(tixcraft.runtime_health, "wait_for_interactive_ready", not_ready)

    await tixcraft.nodriver_tixcraft_ticket_main(tab, _config(), None, None, "tixcraft.com")

    assert navigations == []
    assert tixcraft._has_confirmed_tixcraft_submit_context()


@pytest.mark.asyncio
async def test_transient_route_is_inconclusive_during_submit_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab("https://tixcraft.com/")
    _seed_submitted_ticket_state(tab)

    async def empty_transition_dom(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(tixcraft.runtime_health, "evaluate_with_timeout", empty_transition_dom)

    result = await tixcraft._classify_recovery_page(tab, tab.target.url, PageClass.HOME)

    assert result is PageClass.UNKNOWN


@pytest.mark.asyncio
async def test_probe_timeout_is_not_positive_soft_block_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    async def timed_out_health(*_args, **_kwargs):
        return {"probeFailed": True}

    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", timed_out_health)
    monkeypatch.setattr(tixcraft, "_update_tixcraft_probe_failure_state", lambda *_args: True)

    detection = await tixcraft._detect_tixcraft_soft_block(tab, TICKET_URL, _config())

    assert not detection["blocked"]
    assert detection["inconclusive"]


@pytest.mark.asyncio
async def test_stable_blank_is_not_positive_soft_block_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    async def blank_health(*_args, **_kwargs):
        return {
            "probeFailed": False,
            "blocked": False,
            "whiteOverlay": False,
            "knownOrderProcessing": False,
            "title": "",
            "bodyText": "",
            "elementCount": 0,
            "hasKnownContent": False,
        }

    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", blank_health)
    monkeypatch.setattr(tixcraft, "_update_tixcraft_blank_page_state", lambda *_args: True)

    detection = await tixcraft._detect_tixcraft_soft_block(tab, TICKET_URL, _config())

    assert not detection["blocked"]
    assert detection["inconclusive"]


@pytest.mark.asyncio
async def test_real_soft_block_requires_two_matching_positive_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    async def explicit_eps_block(*_args, **_kwargs):
        return {
            "probeFailed": False,
            "blocked": True,
            "kind": "eps_js",
            "knownOrderProcessing": False,
            "rr": AREA_URL,
            "client_ip": "redacted",
        }

    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", explicit_eps_block)

    first = await tixcraft._detect_tixcraft_soft_block(tab, TICKET_URL, _config())
    second = await tixcraft._detect_tixcraft_soft_block(tab, TICKET_URL, _config())

    assert not first["blocked"]
    assert first["inconclusive"]
    assert second["blocked"]
    assert not second["inconclusive"]


@pytest.mark.asyncio
async def test_order_processing_marker_overrides_soft_block_like_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    tixcraft._state.clear()
    tixcraft._ensure_tixcraft_state_defaults()

    async def processing_snapshot(*_args, **_kwargs):
        return {
            "probeFailed": False,
            "blocked": True,
            "kind": "eps_js",
            "knownOrderProcessing": True,
        }

    monkeypatch.setattr(tixcraft, "_read_tixcraft_page_health", processing_snapshot)

    for _ in range(3):
        detection = await tixcraft._detect_tixcraft_soft_block(
            tab,
            TICKET_URL,
            _config(),
        )
        assert not detection["blocked"]
        assert detection["inconclusive"]
        assert detection["kind"] == "order_processing"


@pytest.mark.asyncio
async def test_order_processing_is_detected_before_ticket_handler_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _Tab()
    ticket_calls: list[str] = []

    async def not_paused(_config):
        return False

    async def no_op(*_args, **_kwargs):
        return None

    async def not_blocked(*_args, **_kwargs):
        return False

    async def ticket_handler(*_args, **_kwargs):
        ticket_calls.append("ticket")

    async def order_processing(*_args, **_kwargs):
        return True

    async def notified(*_args, **_kwargs):
        return True

    monkeypatch.setattr(tixcraft, "check_and_handle_pause", not_paused)
    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_home_close_window", no_op)
    monkeypatch.setattr(tixcraft, "nodriver_ticketmaster_check_ip_block", not_blocked)
    monkeypatch.setattr(tixcraft, "_remember_tixcraft_event_name", no_op)
    monkeypatch.setattr(tixcraft, "nodriver_tixcraft_ticket_main", ticket_handler)
    monkeypatch.setattr(tixcraft, "_detect_tixcraft_order_pending", order_processing)
    monkeypatch.setattr(tixcraft, "_emit_tixcraft_attempt_notification", notified)

    with tixcraft._bind_tixcraft_tab_state(tab):
        _seed_submitted_ticket_state(tab)
        await tixcraft.nodriver_tixcraft_main(tab, TICKET_URL, _config(), None, None)
        await tixcraft.nodriver_tixcraft_main(tab, TICKET_URL, _config(), None, None)

        assert ticket_calls == []
        attempt = tixcraft._get_tixcraft_purchase_attempt()
        assert attempt is not None
        assert attempt.phase.value == "order_pending"


@pytest.mark.asyncio
async def test_concurrent_tabs_bind_distinct_production_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tixcraft._state.clear()
    first = _Tab(TICKET_URL)
    second = _Tab(TICKET_URL.replace("5300", "5301"))
    observed: dict[int, tuple[str, int]] = {}

    async def inspect_bound_state(tab, *_args):
        marker = "first" if tab is first else "second"
        tixcraft._state["tab_marker"] = marker
        await asyncio.sleep(0)
        observed[id(tab)] = (
            tixcraft._state["tab_marker"],
            id(tixcraft._state.current()),
        )
        return False

    monkeypatch.setattr(tixcraft, "_nodriver_tixcraft_main_impl", inspect_bound_state)

    await asyncio.gather(
        tixcraft.nodriver_tixcraft_main(first, first.target.url, _config(), None, None),
        tixcraft.nodriver_tixcraft_main(second, second.target.url, _config(), None, None),
    )

    assert observed[id(first)][0] == "first"
    assert observed[id(second)][0] == "second"
    assert observed[id(first)][1] != observed[id(second)][1]
    assert tixcraft._state_for_tab(first)["tab_marker"] == "first"
    assert tixcraft._state_for_tab(second)["tab_marker"] == "second"
