from __future__ import annotations

from attempt_lifecycle import AttemptState
from page_classifier import PageClass
from runtime_health import (
    EXPECTED_PROGRESS_CAPACITY,
    EXPECTED_PROGRESS_HISTORY_CAPACITY,
    BrowserFailureKind,
    ExpectedProgressKind,
    ExpectedProgressOutcome,
    RecoveryLevel,
    RuntimeHealthState,
    RuntimeHealthSupervisor,
    current_expected_progress_binding,
)


def _arm(
    health: RuntimeHealthSupervisor,
    *,
    token: str = "action-1",
    owner: str = "test-owner",
    attempt_id: str = "attempt-1",
    generation: int = 1,
    deadline: float = 5.0,
    submit_sensitive: bool = False,
    acceptable_routes: tuple[str, ...] = ("/expected",),
):
    return health.arm_expected_progress(
        tab_identity="target:tab-1",
        platform_key="ticketplus",
        attempt_id=attempt_id,
        attempt_generation=generation,
        action_owner=owner,
        action_token=token,
        kind=(
            ExpectedProgressKind.SUBMIT
            if submit_sensitive
            else ExpectedProgressKind.NAVIGATION
        ),
        source_route="/source",
        source_route_generation=4,
        acceptable_routes=acceptable_routes,
        deadline=deadline,
        submit_sensitive=submit_sensitive,
        reconciliation_owner=owner,
        now=0.0,
    )


def test_readable_url_never_clears_an_armed_or_stalled_action() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health) is not None

    health.record_url_success(now=4.0)
    assert health.active_expected_progress_count == 1
    assert health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        current_route="/source",
        route_generation=4,
        page_state=AttemptState.ACTIVITY_READY,
        now=4.0,
    ) == ()

    decisions = health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        current_route="/source",
        route_generation=4,
        page_state=AttemptState.ACTIVITY_READY,
        now=6.0,
    )
    assert [item.outcome for item in decisions] == [
        ExpectedProgressOutcome.STALLED_ACTION
    ]

    health.record_url_success(now=7.0)
    assert health.state is RuntimeHealthState.DEGRADED
    assert health.last_failure_kind is BrowserFailureKind.RENDERER_STALLED
    assert health.unresolved_expected_progress_count == 1
    plan = health.plan_recovery(PageClass.AREA, AttemptState.AREA_READY, now=7.0)
    assert plan.level is RecoveryLevel.REACQUIRE
    assert plan.reason == "expected_action_stalled"


def test_progress_at_the_exact_deadline_confirms_before_expiry() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health, deadline=5.0) is not None

    decisions = health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        current_route="/expected",
        route_generation=5,
        page_state=AttemptState.DATE_READY,
        now=5.0,
    )

    assert [item.outcome for item in decisions] == [
        ExpectedProgressOutcome.CONFIRMED
    ]
    assert health.active_expected_progress_count == 0
    assert health.unresolved_expected_progress_count == 0
    assert health.expected_progress_stall_count == 0
    assert health.last_action_success_at == 5.0


def test_attempt_fence_discards_stale_owner_without_touching_new_attempt() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health, token="old-token") is not None

    decisions = health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-2",
        attempt_generation=2,
        current_route="/source",
        route_generation=4,
        page_state=AttemptState.ACTIVITY_READY,
        now=1.0,
    )

    assert [item.outcome for item in decisions] == [
        ExpectedProgressOutcome.STALE_OWNER
    ]
    assert health.active_expected_progress_count == 0
    assert health.unresolved_expected_progress_count == 0
    assert health.last_failure_kind is BrowserFailureKind.NONE
    assert _arm(
        health,
        token="new-token",
        attempt_id="attempt-2",
        generation=2,
    ) is not None


def test_old_attempt_fault_cannot_drive_recovery_for_new_attempt() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health, token="old-submit", submit_sensitive=True) is not None
    fault = health.fail_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="test-owner",
        action_token="old-submit",
        now=6.0,
    )
    assert fault is not None
    assert health.unresolved_expected_progress_count == 1
    protected_plan = health.plan_recovery(
        PageClass.AREA,
        AttemptState.SUBMIT_OUTCOME_UNKNOWN,
        now=6.0,
    )
    assert protected_plan.level is RecoveryLevel.FAIL_CLOSED
    assert health.state is RuntimeHealthState.DEGRADED

    decisions = health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-2",
        attempt_generation=2,
        current_route="/new-safe-route",
        route_generation=1,
        page_state=AttemptState.ACTIVITY_READY,
        now=7.0,
    )

    assert [item.outcome for item in decisions] == [
        ExpectedProgressOutcome.STALE_OWNER
    ]
    assert health.unresolved_expected_progress_count == 0
    assert health.state is RuntimeHealthState.HEALTHY
    assert health.last_failure_kind is BrowserFailureKind.NONE
    assert health.plan_recovery(
        PageClass.ACTIVITY,
        AttemptState.ACTIVITY_READY,
        now=7.0,
    ).level is RecoveryLevel.NORMAL_RETRY


def test_submit_sensitive_fence_cannot_be_replaced_by_weaker_action() -> None:
    health = RuntimeHealthSupervisor()
    submit = _arm(health, token="submit-token", submit_sensitive=True)
    assert submit is not None

    weaker = health.arm_expected_progress(
        tab_identity="target:tab-1",
        platform_key="ticketplus",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="reload-owner",
        action_token="reload-token",
        kind=ExpectedProgressKind.RELOAD,
        source_route="/source",
        source_route_generation=4,
        minimum_refresh_generation=2,
        deadline=6.0,
        now=1.0,
    )

    assert weaker is None
    same_token_weaker = health.arm_expected_progress(
        tab_identity="target:tab-1",
        platform_key="ticketplus",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="test-owner",
        action_token="submit-token",
        kind=ExpectedProgressKind.NAVIGATION,
        source_route="/source",
        acceptable_routes=("/other",),
        deadline=20.0,
        submit_sensitive=False,
        now=1.0,
    )
    assert same_token_weaker is None
    assert health.active_expected_progress_count == 1
    assert not health.confirm_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="reload-owner",
        action_token="reload-token",
        now=2.0,
    )
    assert health.confirm_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="test-owner",
        action_token="submit-token",
        now=2.0,
    )


def test_submit_fence_supersedes_only_weaker_same_attempt_metadata() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health, token="navigation-token") is not None

    submit = _arm(
        health,
        owner="submit-owner",
        token="submit-token",
        submit_sensitive=True,
    )

    assert submit is not None
    assert health.active_expected_progress_count == 1
    assert health.expected_progress_history[-1].outcome is ExpectedProgressOutcome.CANCELLED
    assert health.expected_progress_history[-1].reason == "superseded_by_submit"


def test_submit_failure_is_fail_closed_and_exactly_fenced() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health, token="submit-token", submit_sensitive=True) is not None

    assert health.fail_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="new-attempt",
        attempt_generation=2,
        action_owner="test-owner",
        action_token="submit-token",
        now=6.0,
    ) is None
    decision = health.fail_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="test-owner",
        action_token="submit-token",
        now=6.0,
    )

    assert decision is not None
    assert decision.outcome is ExpectedProgressOutcome.SUBMIT_OUTCOME_UNKNOWN
    assert decision.requires_fail_closed
    assert health.expected_progress_submit_unknown_count == 1
    assert health.arm_expected_progress(
        tab_identity="target:tab-1",
        platform_key="ticketplus",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="reload-owner",
        action_token="reload-token",
        kind=ExpectedProgressKind.RELOAD,
        deadline=20.0,
        now=7.0,
    ) is None
    health.record_url_success(now=7.0)
    assert health.state is RuntimeHealthState.DEGRADED
    plan = health.plan_recovery(
        PageClass.AREA,
        AttemptState.AREA_READY,
        now=7.0,
    )
    assert plan.level is RecoveryLevel.FAIL_CLOSED
    assert plan.reason == "submit_outcome_unknown"


def test_queue_checkout_and_payment_are_never_reported_as_recoverable_stalls() -> None:
    for state in (
        AttemptState.QUEUE,
        PageClass.ORDER,
        AttemptState.CHECKOUT_REACHED,
        AttemptState.PAYMENT_REACHED,
    ):
        health = RuntimeHealthSupervisor()
        assert _arm(health, deadline=2.0) is not None

        decisions = health.observe_expected_progress(
            tab_identity="target:tab-1",
            attempt_id="attempt-1",
            attempt_generation=1,
            current_route="/protected",
            route_generation=4,
            page_state=state,
            now=2.0,
        )

        assert [item.outcome for item in decisions] == [
            ExpectedProgressOutcome.PROTECTED_NO_RECOVERY
        ]
        assert not decisions[0].permits_recovery
        assert health.expected_progress_stall_count == 0
        assert health.last_failure_kind is BrowserFailureKind.NONE


def test_protected_no_recovery_is_monitor_only_and_new_attempt_rearms() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health, deadline=2.0) is not None

    protected = health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        current_route="/checkout",
        route_generation=4,
        page_state=AttemptState.CHECKOUT_REACHED,
        protected=True,
        now=2.0,
    )
    assert [item.outcome for item in protected] == [
        ExpectedProgressOutcome.PROTECTED_NO_RECOVERY
    ]
    plan = health.plan_recovery(
        PageClass.CHECKOUT,
        AttemptState.CHECKOUT_REACHED,
        now=2.0,
    )
    assert plan.level is RecoveryLevel.FAIL_CLOSED
    assert health.state is RuntimeHealthState.HEALTHY

    stale = health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-2",
        attempt_generation=2,
        current_route="/area",
        route_generation=1,
        page_state=AttemptState.AREA_READY,
        now=3.0,
    )
    assert [item.outcome for item in stale] == [ExpectedProgressOutcome.STALE_OWNER]
    assert health.unresolved_expected_progress_count == 0
    assert health.state is RuntimeHealthState.HEALTHY
    assert health.plan_recovery(
        PageClass.AREA,
        AttemptState.AREA_READY,
        now=3.0,
    ).level is RecoveryLevel.NORMAL_RETRY


def test_legitimate_idle_and_interval_zero_have_no_implicit_timer() -> None:
    health = RuntimeHealthSupervisor()

    # Diagnostic timestamps alone are deliberately not expectations. This is
    # what prevents pre-onsale, leak-watch and interval=0 static pages from
    # becoming false stalls.
    health.record_refresh_attempt(now=1.0)
    for now in (60.0, 3_600.0, 86_400.0):
        health.record_url_success(now=now)
        assert health.observe_expected_progress(
            tab_identity="target:tab-1",
            attempt_id="attempt-1",
            attempt_generation=1,
            current_route="/static",
            route_generation=1,
            page_state=AttemptState.ACTIVITY_READY,
            now=now,
        ) == ()

    assert health.expected_progress_stall_count == 0
    assert health.active_expected_progress_count == 0
    assert health.state is RuntimeHealthState.HEALTHY


def test_confirm_and_cancel_require_exact_attempt_and_action_fences() -> None:
    health = RuntimeHealthSupervisor()
    assert _arm(health, token="a1", attempt_id="attempt-1", generation=1) is not None
    assert _arm(health, token="a2", attempt_id="attempt-2", generation=2) is not None

    assert health.observe_expected_progress(
        tab_identity="target:other-tab",
        attempt_id="attempt-1",
        attempt_generation=1,
        current_route="/expected",
        route_generation=5,
        page_state=AttemptState.DATE_READY,
        now=1.0,
    ) == ()
    assert health.active_expected_progress_count == 2
    assert not health.confirm_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="test-owner",
        action_token="wrong-token",
        now=1.0,
    )
    assert not health.confirm_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-2",
        attempt_generation=2,
        action_owner="test-owner",
        action_token="a1",
        now=1.0,
    )
    assert health.cancel_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        now=1.0,
    ) == 1
    assert health.active_expected_progress_count == 1
    assert health.confirm_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-2",
        attempt_generation=2,
        action_owner="test-owner",
        action_token="a2",
        now=2.0,
    )


def test_overlong_tokens_remain_distinct_compact_fences() -> None:
    health = RuntimeHealthSupervisor()
    shared_prefix = "x" * 300
    first = _arm(health, token=f"{shared_prefix}-one", attempt_id="attempt-1")
    second = _arm(health, token=f"{shared_prefix}-two", attempt_id="attempt-2")

    assert first is not None
    assert second is not None
    assert first.action_token != second.action_token
    assert len(first.action_token) < 100
    assert len(second.action_token) < 100


def test_refresh_generation_is_a_confirmed_completion_condition() -> None:
    health = RuntimeHealthSupervisor()
    expectation = health.arm_expected_progress(
        tab_identity="target:tab-1",
        platform_key="ticketplus",
        attempt_id="attempt-1",
        attempt_generation=1,
        action_owner="refresh-coordinator",
        action_token="refresh-7",
        kind=ExpectedProgressKind.RELOAD,
        source_route="/activity",
        source_route_generation=9,
        minimum_refresh_generation=8,
        deadline=5.0,
        now=0.0,
    )
    assert expectation is not None

    assert health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        current_route="/activity",
        route_generation=9,
        refresh_generation=7,
        page_state=AttemptState.ACTIVITY_READY,
        now=4.0,
    ) == ()
    decisions = health.observe_expected_progress(
        tab_identity="target:tab-1",
        attempt_id="attempt-1",
        attempt_generation=1,
        current_route="/activity",
        route_generation=9,
        refresh_generation=8,
        page_state=AttemptState.ACTIVITY_READY,
        now=5.0,
    )
    assert [item.outcome for item in decisions] == [
        ExpectedProgressOutcome.CONFIRMED
    ]


def test_active_and_history_storage_remain_bounded() -> None:
    health = RuntimeHealthSupervisor()
    for index in range(10_000):
        token = f"token-{index}"
        assert _arm(health, token=token) is not None
        assert health.confirm_expected_progress(
            tab_identity="target:tab-1",
            attempt_id="attempt-1",
            attempt_generation=1,
            action_owner="test-owner",
            action_token=token,
            now=float(index + 1),
        )

    assert health.active_expected_progress_count == 0
    assert health.unresolved_expected_progress_count == 0
    assert len(health.expected_progress_history) == EXPECTED_PROGRESS_HISTORY_CAPACITY

    for index in range(EXPECTED_PROGRESS_CAPACITY):
        assert _arm(
            health,
            token=f"capacity-{index}",
            attempt_id=f"attempt-{index}",
            generation=index + 1,
        ) is not None
    assert _arm(
        health,
        token="over-capacity",
        attempt_id="over-capacity",
        generation=999,
    ) is None
    assert health.active_expected_progress_count == EXPECTED_PROGRESS_CAPACITY


def test_unresolved_fault_storage_cannot_grow_past_capacity() -> None:
    health = RuntimeHealthSupervisor()
    for index in range(EXPECTED_PROGRESS_CAPACITY):
        attempt_id = f"failed-attempt-{index}"
        token = f"failed-token-{index}"
        assert _arm(
            health,
            token=token,
            attempt_id=attempt_id,
            generation=index + 1,
        ) is not None
        decision = health.fail_expected_progress(
            tab_identity="target:tab-1",
            attempt_id=attempt_id,
            attempt_generation=index + 1,
            action_owner="test-owner",
            action_token=token,
            now=float(index + 1),
        )
        assert decision is not None

    assert health.active_expected_progress_count == 0
    assert health.unresolved_expected_progress_count == EXPECTED_PROGRESS_CAPACITY
    assert _arm(
        health,
        token="blocked-by-fault-capacity",
        attempt_id="next-attempt",
        generation=999,
    ) is None
    assert len(health.expected_progress_history) <= EXPECTED_PROGRESS_HISTORY_CAPACITY


def test_idle_observation_fast_path_does_not_even_convert_metadata() -> None:
    class _MustNotBeRead:
        def __str__(self) -> str:
            raise AssertionError("idle observation touched metadata")

    health = RuntimeHealthSupervisor()
    poison = _MustNotBeRead()

    assert health.observe_expected_progress(
        tab_identity=poison,
        attempt_id=poison,
        current_route=poison,
        page_state=poison,
        now=poison,
    ) == ()


def test_task_local_binding_restores_previous_context() -> None:
    first = RuntimeHealthSupervisor()
    second = RuntimeHealthSupervisor()
    assert current_expected_progress_binding() is None

    with first.bind_expected_progress(
        tab_identity="tab-1",
        platform_key="ticketplus",
        attempt_id="attempt-1",
        attempt_generation=1,
    ) as outer:
        assert current_expected_progress_binding() is outer
        with second.bind_expected_progress(
            tab_identity="tab-2",
            platform_key="kktix",
            attempt_id="attempt-2",
            attempt_generation=2,
        ) as inner:
            assert current_expected_progress_binding() is inner
        assert current_expected_progress_binding() is outer

    assert current_expected_progress_binding() is None
