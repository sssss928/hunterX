from __future__ import annotations

from attempt_lifecycle import AttemptRegistry, AttemptState, PurchaseAttempt


def test_purchase_attempt_transition_uses_immutable_dataclass_replacement() -> None:
    attempt = PurchaseAttempt.create_for_area("A1", "event-1")

    updated = attempt.with_state(AttemptState.TICKET_FORM_ACTIVE)

    assert updated is not attempt
    assert attempt.state is AttemptState.AREA_READY
    assert attempt.entered_ticket_page_at is None
    assert updated.state is AttemptState.TICKET_FORM_ACTIVE
    assert updated.entered_ticket_page_at is not None
    assert updated.attempt_id == attempt.attempt_id
    assert updated.area_code == attempt.area_code
    assert updated.event_id == attempt.event_id
    assert updated.created_at == attempt.created_at


def test_attempt_registry_transition_and_close_preserve_identity() -> None:
    registry = AttemptRegistry()
    tab = object()
    created = registry.create_for_area(tab, "B2", "event-2")

    submitted = registry.transition(tab, AttemptState.SUBMIT_IN_FLIGHT)
    assert submitted is not None
    assert submitted.attempt_id == created.attempt_id
    assert submitted.state is AttemptState.SUBMIT_IN_FLIGHT
    assert submitted.submitted_at is not None

    registry.close_attempt(tab)
    closed = registry.get_current(tab)
    assert closed is not None
    assert closed.attempt_id == created.attempt_id
    assert closed.state is AttemptState.CLOSED


def test_attempt_registry_clear_if_stale_is_bounded() -> None:
    registry = AttemptRegistry()
    tab = object()
    created = registry.create_for_area(tab, "C3", "event-3")

    assert registry.clear_if_stale(tab, timeout_seconds=10, now=created.created_at + 9) is False
    assert registry.get_current(tab) is not None
    assert registry.clear_if_stale(tab, timeout_seconds=10, now=created.created_at + 11) is True
    assert registry.get_current(tab) is None
