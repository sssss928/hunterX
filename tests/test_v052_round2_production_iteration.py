from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import nodriver_tixcraft as runtime
from attempt_lifecycle import AttemptState
from browser_session import BrowserExitState, BrowserRecoveryResult
from page_classifier import PageClass
from platform_adapters import adapter_for_key
from platform_engine import platform_engine
from platform_registry import platform_key_for_url
from runtime_health import (
    BrowserFailureKind,
    ExpectedProgressKind,
    RecoveryLevel,
    RuntimeHealthSupervisor,
    current_expected_progress_binding,
)
from settings import get_default_config


class _Target:
    def __init__(self, url: str, target_id: str) -> None:
        self.url = url
        self.target_id = target_id


class _Tab:
    def __init__(self, url: str, target_id: str = "iteration-tab") -> None:
        self.target = _Target(url, target_id)


class _Session:
    def __init__(
        self,
        *,
        recovery_success: bool = False,
        recovery_reason: str = "synthetic_reacquire",
    ) -> None:
        self.recovery_success = recovery_success
        self.recovery_reason = recovery_reason
        self.recover_calls: list[dict[str, object]] = []
        self.attach_calls: list[tuple[object, object | None]] = []

    def browser_exit_state(self) -> BrowserExitState:
        return BrowserExitState.ALIVE

    def attach(self, driver: object, tab: object | None = None) -> None:
        self.attach_calls.append((driver, tab))

    async def recover(
        self,
        level: RecoveryLevel,
        *,
        target_url: str,
        platform_key: str,
        allow_restart: bool,
    ) -> BrowserRecoveryResult:
        self.recover_calls.append(
            {
                "level": level,
                "target_url": target_url,
                "platform_key": platform_key,
                "allow_restart": allow_restart,
            }
        )
        driver, tab = self.attach_calls[-1] if self.attach_calls else (None, None)
        return BrowserRecoveryResult(
            self.recovery_success,
            self.recovery_reason,
            level,
            driver=driver,
            tab=tab,
        )


def _config(
    url: str,
    *,
    run_mode: str = "onsale",
    interval: float = 5.0,
) -> dict:
    config = get_default_config()
    config["homepage"] = url
    config["ocr_captcha"]["enable"] = False
    config["advanced"]["run_mode"] = run_mode
    config["advanced"]["auto_reload_page_interval"] = interval
    config["advanced"]["leak_refresh_interval_seconds"] = interval
    return config


def _context(
    url: str,
    *,
    config: dict | None = None,
    session: _Session | None = None,
    mapper=None,
) -> runtime.RuntimeIterationContext:
    tab = _Tab(url)
    driver = SimpleNamespace(tabs=[tab], main_tab=tab)
    owned_session = session or _Session()
    owned_session.attach(driver, tab)
    return runtime.RuntimeIterationContext(
        config_dict=config or _config(url),
        driver=driver,
        tab=tab,
        session_manager=owned_session,
        health_supervisor=RuntimeHealthSupervisor(
            recovery_cooldown_seconds=0.0,
        ),
        refresh_datetime_state={"target_str": "", "reached": False},
        test_local_route_mapper=mapper,
    )


@pytest.fixture(autouse=True)
def _isolate_runtime_iteration(monkeypatch):
    evidence: dict[str, list] = {"url_reads": []}

    async def _current_url(tab, _config_dict, *, prefer_cached=False):
        evidence["url_reads"].append((tab, prefer_cached))
        return tab.target.url, False

    async def _false(*_args, **_kwargs):
        return False

    monkeypatch.setattr(runtime, "nodriver_current_url", _current_url)
    monkeypatch.setattr(runtime, "check_and_handle_quit", _false)
    monkeypatch.setattr(runtime, "check_and_handle_pause", _false)
    monkeypatch.setattr(runtime, "check_refresh_datetime_gate", _false)
    monkeypatch.setattr(runtime, "detect_cloudflare_challenge", _false)
    monkeypatch.setattr(runtime, "write_last_url_to_file", lambda _url: None)
    monkeypatch.setattr(runtime.util, "get_instance_id", lambda: "default")
    monkeypatch.setattr(
        runtime.runtime_health,
        "get_tab_failure_kind",
        lambda _tab: BrowserFailureKind.NONE,
    )
    return evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("run_mode", ["onsale", "leak_watch"])
@pytest.mark.parametrize(
    ("platform_key", "url", "handler_name"),
    [
        ("ticketplus", "https://ticketplus.com.tw/activity/ITERATION", "nodriver_ticketplus_main"),
        ("tixcraft", "https://tixcraft.com/activity/detail/ITERATION", "nodriver_tixcraft_main"),
        ("kktix", "https://kktix.com/events/ITERATION", "nodriver_kktix_main"),
    ],
)
async def test_authoritative_iteration_dispatches_core_platforms_in_all_modes(
    monkeypatch,
    _isolate_runtime_iteration,
    platform_key,
    url,
    handler_name,
    run_mode,
):
    """Production dispatch, binding, run mode, and zero interval share one path."""

    context = _context(url, config=_config(url, run_mode=run_mode, interval=0.0))
    calls = []

    async def _handler(*args, **_kwargs):
        binding = current_expected_progress_binding()
        assert binding is not None
        assert binding.platform_key == platform_key
        assert binding.attempt_id
        assert binding.attempt_generation == 1
        calls.append(args)
        return {} if platform_key == "ticketplus" else False

    monkeypatch.setattr(runtime, handler_name, _handler)
    result = await runtime.run_runtime_iteration(context)

    assert result.action == "dispatched"
    assert result.platform_key == platform_key
    assert result.page_class in {PageClass.ACTIVITY, PageClass.DATE}
    assert result.new_attempt_started is True
    assert len(calls) == 1
    assert _isolate_runtime_iteration["url_reads"] == [(context.tab, False)]
    assert current_expected_progress_binding() is None


@pytest.mark.asyncio
async def test_terminal_platform_exception_propagates_from_iteration(monkeypatch):
    url = "https://ticketplus.com.tw/activity/TERMINAL"
    context = _context(url)

    async def _terminal(*_args, **_kwargs):
        raise ConnectionError("WebSocket connection closed")

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _terminal)
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await runtime.run_runtime_iteration(context)


@pytest.mark.asyncio
async def test_pause_and_refresh_gate_block_normal_platform_dispatch(monkeypatch):
    url = "https://kktix.com/events/GATED"
    context = _context(url)
    calls = []

    async def _paused(*_args, **_kwargs):
        calls.append("paused")

    async def _normal(*_args, **_kwargs):
        calls.append("normal")
        return False

    async def _true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(runtime, "nodriver_kktix_paused_main", _paused)
    monkeypatch.setattr(runtime, "nodriver_kktix_main", _normal)
    monkeypatch.setattr(runtime, "check_and_handle_pause", _true)
    paused = await runtime.run_runtime_iteration(context)
    assert paused.reason == "paused"
    assert calls == ["paused"]

    monkeypatch.setattr(runtime, "check_and_handle_pause", lambda *_args, **_kwargs: None)

    async def _not_paused(*_args, **_kwargs):
        return False

    monkeypatch.setattr(runtime, "check_and_handle_pause", _not_paused)
    monkeypatch.setattr(runtime, "check_refresh_datetime_gate", _true)
    gated = await runtime.run_runtime_iteration(context)
    assert gated.reason == "refresh_gate"
    assert calls == ["paused"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform_key", "url", "handler_name", "returns_tab"),
    [
        ("famiticket", "https://www.famiticket.com.tw/activity/demo", "nodriver_famiticket_main", False),
        ("ibon", "https://ticket.ibon.com.tw/ActivityInfo/Details/demo", "nodriver_ibon_main", False),
        ("kham", "https://kham.com.tw/application/UTK01/UTK0101_03.aspx?PRODUCT_ID=demo", "nodriver_kham_main", True),
        ("cityline", "https://www.cityline.com/event/demo", "nodriver_cityline_main", True),
        ("hkticketing", "https://premier.hkticketing.com/events/demo", "nodriver_hkticketing_main", True),
        ("funone", "https://tickets.funone.io/events/demo", "nodriver_funone_main", True),
        ("fansigo", "https://go.fansi.me/events/demo", "nodriver_fansigo_main", True),
    ],
)
async def test_registry_families_use_the_production_dispatch_branch(
    monkeypatch,
    platform_key,
    url,
    handler_name,
    returns_tab,
):
    context = _context(url)
    calls = []

    async def _handler(*args, **_kwargs):
        calls.append(args)
        return args[0] if returns_tab else None

    monkeypatch.setattr(runtime, handler_name, _handler)
    result = await runtime.run_runtime_iteration(context)

    assert result.action == "dispatched"
    assert result.platform_key == platform_key
    assert len(calls) == 1


def _arm_progress(
    context: runtime.RuntimeIterationContext,
    *,
    action_token: str,
    submit_sensitive: bool = False,
):
    attempt = platform_engine.current_attempt(context.tab, "ticketplus")
    assert attempt is not None
    state = platform_engine.state_for(context.tab, adapter_for_key("ticketplus"))
    expectation = context.health_supervisor.arm_expected_progress(
        tab_identity=runtime._iteration_tab_identity(context.tab),
        platform_key="ticketplus",
        attempt_id=attempt.attempt_id,
        attempt_generation=attempt.generation,
        action_owner="iteration-test-owner",
        action_token=action_token,
        kind=(ExpectedProgressKind.SUBMIT if submit_sensitive else ExpectedProgressKind.RELOAD),
        source_route=context.tab.target.url,
        source_route_generation=state.route_generation,
        acceptable_routes=("https://ticketplus.com.tw/activity/NEVER",),
        deadline=0.0,
        submit_sensitive=submit_sensitive,
        reconciliation_owner="production_iteration",
        now=0.0,
    )
    assert expectation is not None
    return attempt, expectation


@pytest.mark.asyncio
async def test_readable_stall_reacquire_success_reconciles_exact_fault(monkeypatch):
    url = "https://ticketplus.com.tw/activity/STALL"
    session = _Session(recovery_success=True)
    context = _context(url, session=session)
    dispatch_count = 0

    async def _handler(*_args, **_kwargs):
        nonlocal dispatch_count
        dispatch_count += 1
        return {}

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _handler)
    await runtime.run_runtime_iteration(context)
    _arm_progress(context, action_token="reload-stall")

    result = await runtime.run_runtime_iteration(context)

    assert result.action == "continue"
    assert result.recovery_level is RecoveryLevel.REACQUIRE
    assert dispatch_count == 1
    assert len(session.recover_calls) == 1
    assert session.recover_calls[0]["allow_restart"] is False
    assert context.pending_progress_fault is None
    assert context.health_supervisor.unresolved_expected_progress_count == 0


@pytest.mark.asyncio
async def test_readable_stall_reacquire_failure_never_silently_dispatches(monkeypatch):
    url = "https://ticketplus.com.tw/activity/STALL-FAIL"
    session = _Session(recovery_success=False, recovery_reason="proof_failed")
    context = _context(url, session=session)
    dispatch_count = 0

    async def _handler(*_args, **_kwargs):
        nonlocal dispatch_count
        dispatch_count += 1
        return {}

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _handler)
    await runtime.run_runtime_iteration(context)
    _arm_progress(context, action_token="reload-stall-fail")

    first_failure = await runtime.run_runtime_iteration(context)
    second_failure = await runtime.run_runtime_iteration(context)

    assert first_failure.action == second_failure.action == "monitor"
    assert first_failure.recovery_level is RecoveryLevel.REACQUIRE
    assert dispatch_count == 1
    assert context.pending_progress_fault is not None
    assert context.health_supervisor.unresolved_expected_progress_count == 1
    assert all(call["allow_restart"] is False for call in session.recover_calls)


@pytest.mark.asyncio
async def test_protected_deadline_is_read_only_then_new_safe_attempt_resumes(monkeypatch):
    safe_url = "https://ticketplus.com.tw/activity/PROTECTED-A"
    context = _context(safe_url)
    calls = []

    async def _handler(*_args, **_kwargs):
        calls.append(context.tab.target.url)
        return {}

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _handler)
    await runtime.run_runtime_iteration(context)
    old_attempt, _ = _arm_progress(context, action_token="protected-deadline")

    context.tab.target.url = "https://ticketplus.com.tw/confirm/ORDER-A"
    protected = await runtime.run_runtime_iteration(context)
    assert protected.action == "monitor"
    assert protected.reason == "protected_no_recovery"
    assert context.session_manager.recover_calls == []
    assert len(calls) == 1

    context.tab.target.url = "https://ticketplus.com.tw/activity/PROTECTED-B"
    resumed = await runtime.run_runtime_iteration(context)
    current = platform_engine.current_attempt(context.tab, "ticketplus")

    assert resumed.action == "dispatched"
    assert current is not None
    assert current.attempt_id != old_attempt.attempt_id
    assert current.generation > old_attempt.generation
    assert context.health_supervisor.unresolved_expected_progress_count == 0
    assert context.pending_progress_fault is None
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protected_url", "expected_page"),
    [
        ("https://ticketplus.com.tw/confirm/NO-RECOVERY", PageClass.CHECKOUT),
        ("https://ticketplus.com.tw/payment/NO-RECOVERY", PageClass.PAYMENT),
        ("https://ticketplus.com.tw/queue?queue=1", PageClass.QUEUE),
    ],
)
async def test_protected_routes_never_reload_reacquire_or_restart(
    monkeypatch,
    protected_url,
    expected_page,
):
    safe_url = "https://ticketplus.com.tw/activity/NO-RECOVERY"
    context = _context(safe_url)
    calls = []

    async def _handler(*_args, **_kwargs):
        calls.append(context.tab.target.url)
        return {}

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _handler)
    await runtime.run_runtime_iteration(context)
    _arm_progress(context, action_token="protected-no-mutation")

    context.tab.target.url = protected_url
    result = await runtime.run_runtime_iteration(context)

    assert result.action == "monitor"
    assert result.reason == "protected_no_recovery"
    assert result.page_class is expected_page
    assert context.session_manager.recover_calls == []
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_submit_timeout_is_exact_unknown_and_safe_rearm_has_no_old_owner(monkeypatch):
    first_url = "https://ticketplus.com.tw/activity/SUBMIT-A"
    context = _context(first_url)
    calls = []

    async def _handler(*_args, **_kwargs):
        calls.append(context.tab.target.url)
        return {}

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _handler)
    await runtime.run_runtime_iteration(context)
    old_attempt = platform_engine.current_attempt(context.tab, "ticketplus")
    assert old_attempt is not None
    old_token = platform_engine.claim_submit(
        context.tab,
        "ticketplus",
        owner="production-submit-owner",
    )
    assert old_token
    old_attempt = platform_engine.current_attempt(context.tab, "ticketplus")
    assert old_attempt is not None
    _arm_progress(context, action_token=old_token, submit_sensitive=True)

    unknown_result = await runtime.run_runtime_iteration(context)
    unknown_attempt = platform_engine.current_attempt(context.tab, "ticketplus")
    assert unknown_result.action == "monitor"
    assert unknown_result.reason == "submit_outcome_unknown"
    assert unknown_attempt is not None
    assert unknown_attempt.state is AttemptState.SUBMIT_OUTCOME_UNKNOWN
    assert platform_engine.claim_submit(
        context.tab,
        "ticketplus",
        owner="duplicate-owner",
    ) is None
    assert context.session_manager.recover_calls == []
    assert len(calls) == 1

    safe_url = "https://ticketplus.com.tw/activity/SUBMIT-B"
    assert platform_engine.require_positive_safe_rearm_proof(
        context.tab,
        "ticketplus",
        attempt_id=unknown_attempt.attempt_id,
        attempt_generation=unknown_attempt.generation,
        token=old_token,
        owner="ticketplus-positive-proof",
    )
    replacement = platform_engine.confirm_positive_safe_rearm_if_owned(
        context.tab,
        "ticketplus",
        attempt_id=unknown_attempt.attempt_id,
        attempt_generation=unknown_attempt.generation,
        token=old_token,
        url=safe_url,
        page_class=PageClass.ACTIVITY,
    )
    assert replacement is not None
    context.tab.target.url = safe_url

    resumed = await runtime.run_runtime_iteration(context)
    current = platform_engine.current_attempt(context.tab, "ticketplus")
    assert resumed.action == "dispatched"
    assert current is not None
    assert current.attempt_id == replacement.attempt_id
    assert current.attempt_id != unknown_attempt.attempt_id
    assert context.health_supervisor.unresolved_expected_progress_count == 0
    assert context.pending_progress_fault is None
    new_token = platform_engine.claim_submit(
        context.tab,
        "ticketplus",
        owner="new-submit-owner",
    )
    assert new_token and new_token != old_token
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_submit_unknown_fence_mismatch_cannot_mutate_current_owner(monkeypatch):
    url = "https://ticketplus.com.tw/activity/SUBMIT-MISMATCH"
    context = _context(url)
    calls = []

    async def _handler(*_args, **_kwargs):
        calls.append(True)
        return {}

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _handler)
    await runtime.run_runtime_iteration(context)
    real_token = platform_engine.claim_submit(
        context.tab,
        "ticketplus",
        owner="real-submit-owner",
    )
    assert real_token
    current = platform_engine.current_attempt(context.tab, "ticketplus")
    assert current is not None
    _arm_progress(context, action_token="stale-submit-token", submit_sensitive=True)

    result = await runtime.run_runtime_iteration(context)
    still_current = platform_engine.current_attempt(context.tab, "ticketplus")

    assert result.action == "monitor"
    assert result.reason == "submit_owner_fence_mismatch"
    assert result.recovery_level is RecoveryLevel.FAIL_CLOSED
    assert still_current is not None
    assert still_current.attempt_id == current.attempt_id
    assert still_current.state is AttemptState.SUBMIT_IN_FLIGHT
    assert still_current.submit_token == real_token
    assert context.session_manager.recover_calls == []
    assert calls == [True]


@pytest.mark.asyncio
async def test_local_route_mapper_is_loopback_only(monkeypatch):
    public = _context(
        "https://example.com/local-fixture",
        mapper=lambda _url: "https://ticketplus.com.tw/activity/MAPPED",
    )
    with pytest.raises(RuntimeError, match="restricted to loopback"):
        await runtime.run_runtime_iteration(public)

    local = _context(
        "http://127.0.0.1:8765/synthetic_ticket_spa.html",
        mapper=lambda _url: "https://ticketplus.com.tw/activity/MAPPED",
    )
    calls = []

    async def _handler(*_args, **_kwargs):
        calls.append(True)
        return {}

    monkeypatch.setattr(runtime, "nodriver_ticketplus_main", _handler)
    mapped = await runtime.run_runtime_iteration(local)
    assert mapped.platform_key == "ticketplus"
    assert mapped.actual_url.startswith("http://127.0.0.1:")
    assert calls == [True]
    assert platform_key_for_url(mapped.actual_url) is None


def test_soak_and_outer_loop_share_iteration_without_manual_lifecycle() -> None:
    root = Path(runtime.__file__).resolve().parents[1]
    soak_path = root / "scripts" / "v052_browser_soak.py"
    soak_tree = ast.parse(soak_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(soak_tree)
        if isinstance(node, ast.ImportFrom) and node.module == "nodriver_tixcraft"
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(soak_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(soak_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert {"RuntimeIterationContext", "run_runtime_iteration"} <= imported
    assert "run_runtime_iteration" in called_names
    assert "PlatformEngine" not in imported | called_names
    assert {
        "before_dispatch",
        "claim_submit",
        "mark_attempt_completed",
        "mark_submit_outcome_unknown",
    }.isdisjoint(called_attributes)

    outer_loop = inspect.getsource(runtime._run_main)
    assert outer_loop.count("await run_runtime_iteration(") == 1
    assert "nodriver_current_url(" not in outer_loop
    assert "platform_engine.before_dispatch(" not in outer_loop
