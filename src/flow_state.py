from __future__ import annotations

from enum import Enum


class FlowState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    AREA_SCANNING = "AREA_SCANNING"
    AREA_CLICK_PENDING = "AREA_CLICK_PENDING"
    TICKET_FORM_READY = "TICKET_FORM_READY"
    CAPTCHA_OR_FORM_FILLING = "CAPTCHA_OR_FORM_FILLING"
    SUBMIT_PENDING = "SUBMIT_PENDING"
    ORDER_PENDING = "ORDER_PENDING"
    CHECKOUT_REACHED = "CHECKOUT_REACHED"
    PAYMENT_REACHED = "PAYMENT_REACHED"
    RECOVERING_TO_AREA = "RECOVERING_TO_AREA"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"
    STOPPED_BY_USER = "STOPPED_BY_USER"


class FlowStateMachine:
    """Small runtime state holder. It must not own browser lifecycle calls."""

    def __init__(self) -> None:
        self.state = FlowState.IDLE
        self.previous_state = FlowState.IDLE

    def transition(self, state: FlowState | str) -> FlowState:
        next_state = FlowState(state)
        if next_state != self.state:
            self.previous_state = self.state
            self.state = next_state
        return self.state

    def reset_running(self) -> FlowState:
        return self.transition(FlowState.RUNNING)

    @property
    def is_stopped(self) -> bool:
        return self.state == FlowState.STOPPED_BY_USER
